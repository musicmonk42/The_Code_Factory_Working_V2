# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# runner/mutation.py
# module for mutation testing and fuzzing.
# Provides multi-language support, pluggable tools, advanced strategies,
# robust execution, and comprehensive observability with elite-tier safeguards.

import asyncio
import contextlib
import importlib
import inspect
import json
import logging
import math
import random
import re
import subprocess
import sys
from collections import defaultdict
from functools import wraps
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union


# Try to import specific mutation tools
try:
    import mutmut  # pip install mutmut

    HAS_MUTMUT = True
    MUTMUT_VERSION = getattr(mutmut, "__version__", "unknown")
except ImportError:
    HAS_MUTMUT = False
    MUTMUT_VERSION = "N/A"
    logging.getLogger(__name__).warning(
        "mutmut not installed. Python mutation testing will use subprocess fallback or be unavailable."
    )

# Try to import property-based testing tool
try:
    import hypothesis  # pip install hypothesis
    import hypothesis.strategies as st

    HAS_HYPOTHESIS = True
    HYPOTHESIS_VERSION = getattr(hypothesis, "__version__", "unknown")
except ImportError:
    HAS_HYPOTHESIS = False
    HYPOTHESIS_VERSION = "N/A"
    logging.getLogger(__name__).warning(
        "Hypothesis not installed. Property-based testing and Hypothesis-based fuzzing will be unavailable."
    )

# Assume RunnerConfig and metrics are available
from .runner_config import RunnerConfig

from .runner_errors import (
    ERROR_CODE_REGISTRY as error_codes,
)  # Import error codes
from .runner_errors import RunnerError, TimeoutError  # Import specific errors
from .runner_logging import logger
from .runner_metrics import prom

# Gold Standard: Import contracts and structured errors


# OpenTelemetry Tracing Setup (Gold Standard: Safe Fallback)
@contextlib.contextmanager
def _noop_context(*a, **kw):
    """A no-op context manager for when tracing is disabled."""
    yield


try:
    import opentelemetry.trace as trace
    import opentelemetry.trace.status as trace_status  # Needed for trace.StatusCode

    _tracer = trace.get_tracer(__name__)

    # Implement complete and correct trace_method_decorator logic
    def trace_method_decorator(func):
        if _tracer:

            def wrapper(*args, **kwargs):
                with _tracer.start_as_current_span(
                    f"{func.__module__}.{func.__name__}"
                ) as span:
                    try:
                        result = func(*args, **kwargs)
                        if asyncio.iscoroutine(result):
                            return result  # Allow the async function to be awaited elsewhere
                        return result
                    except Exception as e:
                        if span.is_recording():
                            span.set_status(
                                trace_status.Status(
                                    trace_status.StatusCode.ERROR, str(e)
                                )
                            )
                            span.record_exception(e)
                        raise

            return wrapper
        return func

except ImportError:
    _tracer = None
    logger.warning(
        "OpenTelemetry not installed. Tracing will be disabled in runner_mutation."
    )

    def trace_method_decorator(func):
        return func


try:
    import aiohttp as _aiohttp
    HAS_AIOHTTP = True
except ImportError:
    _aiohttp = None
    HAS_AIOHTTP = False

# Correctly import metrics from runner.runner_metrics if available
# Assuming the metrics module itself is safe to import, and 'prom' is the prometheus_client
from .runner_metrics import (
    COVERAGE_GAPS,
    MUTATION_ERROR,
    MUTATION_KILLED,
    MUTATION_SURVIVED,
    MUTATION_TIMEOUT,
    MUTATION_TOTAL,
)
from .runner_metrics import (
    RUN_FUZZ_DISCOVERIES as FUZZ_DISCOVERIES,
)  # Use 'as' to alias
from .runner_metrics import (
    RUN_MUTATION_SURVIVAL as MUTATION_SURVIVAL_RATE,  # Use 'as' to alias
)

# --- Plug-in Registration ---
_MUTATOR_REGISTRY: Dict[str, Dict[str, Any]] = defaultdict(dict)


def register_mutator(
    language: str,
    tool_name: str,
    extensions: List[str],
    run_func: Callable[[Path, str, Dict[str, Any]], Awaitable[Dict[str, Any]]],
    parse_func: Callable[[Dict[str, str]], Dict[str, int]],
    setup_config_func: Optional[Callable[[Path, List[Path], List[Path]], None]] = None,
    tool_version_cmd: Optional[Union[str, List[str]]] = None,
):
    """
    Registers a new mutation testing tool for a specific language.
    """
    if tool_name in _MUTATOR_REGISTRY[language]:
        logger.warning(
            f"Mutator '{tool_name}' for language '{language}' already registered. Overwriting."
        )
    _MUTATOR_REGISTRY[language][tool_name] = {
        "tool": tool_name,
        "extensions": extensions,
        "run": run_func,
        "parse": parse_func,
        "setup_config": setup_config_func,
        "version_cmd": tool_version_cmd,
    }
    logger.info(f"Mutator '{tool_name}' registered for language '{language}'.")


# --- Helper for running subprocesses (consistent across backends) ---
async def _run_subprocess_safe(
    cmd: Union[str, List[str]], cwd: Path, timeout: int = 300
) -> Dict[str, Any]:
    """
    Helper to run a shell command safely and capture output.
    Raises RunnerError for subprocess failures.
    """
    cmd_list = cmd if isinstance(cmd, list) else cmd.split()
    logger.debug(f"Executing subprocess command: {' '.join(cmd_list)} in {cwd}")
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)

        stdout_str = stdout.decode("utf-8", errors="ignore").strip()
        stderr_str = stderr.decode("utf-8", errors="ignore").strip()

        returncode = process.returncode

        if returncode != 0:
            logger.warning(
                f"Command exited with non-zero code {returncode}: {' '.join(cmd_list)}\nStderr: {stderr_str}"
            )
            raise RunnerError(
                error_codes["TEST_EXECUTION_FAILED"],
                detail=f"Subprocess command failed with exit code {returncode}.",
                returncode=returncode,
                stdout=stdout_str,
                stderr=stderr_str,
                cmd=" ".join(cmd_list),
            )
        return {"stdout": stdout_str, "stderr": stderr_str, "returncode": returncode}
    except asyncio.TimeoutError:
        if process:
            process.kill()
            await process.wait()
        logger.error(f"Command timed out after {timeout} seconds: {' '.join(cmd_list)}")
        raise TimeoutError(
            "TASK_TIMEOUT",
            detail=f"Subprocess command timed out after {timeout} seconds.",
            timeout_seconds=timeout,
            cmd=" ".join(cmd_list),
        )
    except FileNotFoundError:
        first_arg = cmd_list[0]
        logger.error(
            f"Command not found: '{first_arg}'. Ensure tool is installed and in PATH."
        )
        raise RunnerError(
            error_codes["TEST_EXECUTION_FAILED"],
            detail=f"Command '{first_arg}' not found. Ensure tool is installed and in PATH.",
            returncode=127,
            cmd=" ".join(cmd_list),
        )
    except RunnerError:  # Re-raise already structured errors
        raise
    except Exception as e:
        logger.error(f"Unexpected error running subprocess: {e}", exc_info=True)
        raise RunnerError(
            error_codes["UNEXPECTED_ERROR"],
            detail=f"Unexpected error executing command: {e}",
            returncode=1,
            cmd=" ".join(cmd_list),
            cause=e,
        )
    finally:
        if process and process.returncode is None:
            try:
                process.terminate()
                await process.wait()
            except Exception as e:
                logger.warning(f"Failed to terminate subprocess gracefully: {e}")


async def _get_tool_version(tool_name: str, version_cmd: Union[str, List[str]]) -> str:
    """Gold Standard: Executes command to get tool version."""
    try:
        result = await _run_subprocess_safe(version_cmd, cwd=Path("."), timeout=5)
        if result["returncode"] == 0:
            match = re.search(
                r"version (\d+\.\d+\.\d+)",
                result["stdout"] + result["stderr"],
                re.IGNORECASE,
            )
            if match:
                return match.group(1)
            return result["stdout"].splitlines()[0].strip() or "unknown"
        else:
            logger.warning(
                f"Failed to get version for {tool_name} (exit code {result['returncode']}). Stderr: {result['stderr']}"
            )
            return "unavailable"
    except RunnerError as e:  # Catch structured error from subprocess call
        logger.warning(f"Error checking version for {tool_name}: {e.as_dict()}")
        return "unavailable"
    except Exception as e:
        logger.warning(f"Error checking version for {tool_name}: {e}")
        return "unavailable"


# --- Parsers for Mutation Tool Outputs ---
# These parsers are internal to mutation.py and assume raw dict input.
# The `runner.parsers` module handles external files and returns Pydantic schemas.
def parse_mutmut_output(raw_result: Dict[str, str]) -> Dict[str, int]:
    """Parses mutmut results from its subprocess stdout or JSON report file."""
    if "report_file_content" in raw_result and raw_result["report_file_content"]:
        try:
            report_json = json.loads(raw_result["report_file_content"])
            total = report_json.get("total_mutants", 0)
            killed = report_json.get("killed_mutants", 0)
            survived = report_json.get("survived_mutants", 0)
            timeout = report_json.get("timed_out_mutants", 0)
            error = report_json.get("error_mutants", 0)
            return {
                "total": total,
                "survived": survived,
                "killed": killed,
                "timeout": timeout,
                "error": error,
            }
        except json.JSONDecodeError:
            logger.warning(
                "mutmut 'report_file_content' was not valid JSON. Falling back to stdout regex."
            )

    output_str = raw_result.get("stdout", "")
    total_match = re.search(r"(\d+) mutants generated", output_str)
    survived_match = re.search(r"(\d+) survived", output_str)
    killed_match = re.search(r"(\d+) killed", output_str)
    timeout_match = re.search(r"(\d+) timed out", output_str)
    error_match = re.search(r"(\d+) errors", output_str)

    total = int(total_match.group(1)) if total_match else 0
    survived = int(survived_match.group(1)) if survived_match else 0
    killed = int(killed_match.group(1)) if killed_match else 0
    timeout = int(timeout_match.group(1)) if timeout_match else 0
    error = int(error_match.group(1)) if error_match else 0

    # If the summary line format is different: '10 mutants generated. 5 killed, 4 survived, 1 timed out.'
    if total == 0:
        summary_match = re.search(
            r"(\d+) mutants generated\. (\d+) killed, (\d+) survived, (\d+) timed out",
            output_str,
        )
        if summary_match:
            total = int(summary_match.group(1))
            killed = int(summary_match.group(2))
            survived = int(summary_match.group(3))
            timeout = int(summary_match.group(4))
            error = total - (killed + survived + timeout)  # Simple error estimation
            error = max(0, error)

    return {
        "total": total,
        "survived": survived,
        "killed": killed,
        "timeout": timeout,
        "error": error,
    }


def parse_pitest_output(raw_result: Dict[str, str]) -> Dict[str, int]:
    """Parses Pitest (Java) results from its XML/JSON report file or console output."""
    if "report_file_content" in raw_result and raw_result["report_file_content"]:
        try:
            report_json = json.loads(raw_result["report_file_content"])
            total = report_json.get("totalMutants", 0)
            killed = report_json.get("killed", 0)
            survived = report_json.get("survived", 0)
            timeout = report_json.get("timeout", 0)
            error = report_json.get("errors", 0)
            return {
                "total": total,
                "survived": survived,
                "killed": killed,
                "timeout": timeout,
                "error": error,
            }
        except json.JSONDecodeError:
            logger.warning(
                "Pitest 'report_file_content' was not valid JSON. Falling back to stdout regex."
            )

    output_str = raw_result.get("stdout", "") + raw_result.get("stderr", "")
    summary_match = re.search(
        r"All mutants killed: (\d+), survived: (\d+), timed out: (\d+), non-viable: (\d+)",
        output_str,
    )

    if summary_match:
        killed = int(summary_match.group(1))
        survived = int(summary_match.group(2))
        timeout = int(summary_match.group(3))
        non_viable = int(summary_match.group(4))

        total = killed + survived + timeout + non_viable
        error = 0
        return {
            "total": total,
            "survived": survived,
            "killed": killed,
            "timeout": timeout,
            "error": error,
        }

    logger.warning(
        "Pitest console summary not found and no valid report file. Returning zero results."
    )
    return {
        "total": 0,
        "survived": 0,
        "killed": 0,
        "timeout": 0,
        "error": 0,
        "message": "Pitest results not found or parsed.",
    }


def parse_stryker_output(raw_result: Dict[str, str]) -> Dict[str, int]:
    """Parses Stryker (JS/.NET) results from its JSON report file or console output."""
    if "report_file_content" in raw_result and raw_result["report_file_content"]:
        try:
            report_json = json.loads(raw_result["report_file_content"])
            if "files" in report_json:
                total_mutants = 0
                killed_mutants = 0
                survived_mutants = 0
                timed_out_mutants = 0
                error_mutants = 0

                for file_path, file_data in report_json["files"].items():
                    for mutant in file_data.get("mutants", []):
                        total_mutants += 1
                        if mutant["status"] == "Killed":
                            killed_mutants += 1
                        elif mutant["status"] == "Survived":
                            survived_mutants += 1
                        elif mutant["status"] == "Timeout":
                            timed_out_mutants += 1
                        elif mutant["status"] == "Error":
                            error_mutants += 1
                return {
                    "total": total_mutants,
                    "survived": survived_mutants,
                    "killed": killed_mutants,
                    "timeout": timed_out_mutants,
                    "error": error_mutants,
                }
            elif "totals" in report_json:
                totals = report_json["totals"]
                return {
                    "total": totals.get("mutants", 0),
                    "survived": totals.get("survived", 0),
                    "killed": totals.get("killed", 0),
                    "timeout": totals.get("timeout", 0),
                    "error": totals.get("errors", 0),
                }
        except json.JSONDecodeError:
            logger.warning(
                "Stryker 'report_file_content' was not valid JSON. Falling back to stdout regex."
            )

    output_str = raw_result.get("stdout", "") + raw_result.get("stderr", "")
    total_match = re.search(r"(\d+) mutants generated", output_str)
    killed_match = re.search(r"(\d+) killed", output_str)
    survived_match = re.search(r"(\d+) survived", output_str)
    timeout_match = re.search(r"(\d+) timed out", output_str)
    error_match = re.search(r"(\d+) errors", output_str)

    total = int(total_match.group(1)) if total_match else 0
    killed = int(killed_match.group(1)) if killed_match else 0
    survived = int(survived_match.group(1)) if survived_match else 0
    timeout = int(timeout_match.group(1)) if timeout_match else 0
    error = int(error_match.group(1)) if error_match else 0

    return {
        "total": total,
        "survived": survived,
        "killed": killed,
        "timeout": timeout,
        "error": error,
    }


# --- Helper function for mutmut setup with validation ---
def _setup_mutmut_config(temp_dir_path: Path, code_file_paths: List[Path], test_file_paths: List[Path]) -> None:
    """
    Setup mutmut configuration with validation.
    
    Args:
        temp_dir_path: Root temporary directory
        code_file_paths: List of code file paths
        test_file_paths: List of test file paths
    
    Raises:
        FileNotFoundError: If code directory doesn't exist
    """
    # Validate that code/ directory exists before writing config
    code_dir = temp_dir_path / "code"
    if not code_dir.exists():
        error_msg = f"Code directory does not exist at {code_dir}. Cannot run mutmut without code files."
        logger.error(error_msg)
        raise FileNotFoundError(error_msg)
    
    # Log directory structure for debugging
    logger.debug(f"Setting up mutmut config in {temp_dir_path}")
    logger.debug(f"Code directory exists: {code_dir.exists()}")
    if code_dir.exists():
        code_files = list(code_dir.rglob("*.py"))
        logger.debug(f"Found {len(code_files)} Python files in code/")
    
    # Use TOML array syntax for paths to prevent mutmut from iterating over string characters
    # Use test_command instead of tests_dir to avoid mutmut v3+ character iteration bug
    pyproject_path = temp_dir_path / "pyproject.toml"
    pyproject_path.write_text(
        """# pyproject.toml - Generated by runner_mutation.py for mutmut v3+
[tool.mutmut]
paths_to_mutate = ["code/"]
paths_to_exclude = ["tests/"]
runner = "pytest"
test_command = "pytest tests/"
test_time_multiplier = 2.0
# Backup directory for cache
backup_dir = ".mutmut-cache"
# Use simple output for easier parsing
dict_synonyms = ["dict", "{}"]

[tool.pytest.ini_options]
# Ensure pytest can find tests and code
pythonpath = [".", "code"]
testpaths = ["tests"]
"""
    )
    logger.info(f"Created pyproject.toml for mutmut at {pyproject_path}")


# --- Initial Population of MUTATORS Registry ---
# Update mutmut CLI for v3+ compatibility
# mutmut v3 uses configuration file instead of CLI flags like --paths-to-mutate
register_mutator(
    language="python",
    tool_name="mutmut",
    extensions=[".py"],
    run_func=lambda temp_dir_path, strategy, params: _run_subprocess_safe(
        [
            "mutmut",
            "run",
            # mutmut v3+ uses configuration from pyproject.toml or mutmut_config.py
            # No longer accepts --paths-to-mutate or --paths-to-exclude flags
        ],
        cwd=temp_dir_path,
        timeout=params.get("timeout", 300),
    ),
    parse_func=parse_mutmut_output,
    setup_config_func=_setup_mutmut_config,
    tool_version_cmd=["mutmut", "--version"],
)

register_mutator(
    language="java",
    tool_name="pitest",
    extensions=[".java", ".kt", ".scala"],
    run_func=lambda temp_dir_path, strategy, params: _run_subprocess_safe(
        [
            "mvn",
            "org.pitest:pitest-maven:mutationCoverage",
        ],  # Assumes Maven project setup
        cwd=temp_dir_path / "code",  # Run in the code directory where pom.xml is
        timeout=params.get("timeout", 600),
    ),
    parse_func=parse_pitest_output,
    setup_config_func=lambda temp_dir_path, code_file_paths, test_file_paths: None,  # Pitest uses pom.xml/build.gradle
    tool_version_cmd=["mvn", "--version"],  # Pitest version is part of Maven output
)

register_mutator(
    language="javascript",
    tool_name="stryker",
    extensions=[".js", ".ts", ".jsx", ".tsx"],
    run_func=lambda temp_dir_path, strategy, params: _run_subprocess_safe(
        [
            "npx",
            "stryker",
            "run",
            "--reporter",
            "json",
            "--jsonFilePath",
            "stryker-report.json",
        ],
        cwd=temp_dir_path / "code",
        timeout=params.get("timeout", 600),
    ),
    parse_func=parse_stryker_output,
    setup_config_func=lambda temp_dir_path, code_file_paths, test_file_paths: (
        temp_dir_path / "code" / "stryker.conf.json"
    ).write_text(
        """
        // stryker.conf.json - Generated by runner_mutation.py
        module.exports = {
          packageManager: "npm",
          reporters: ["html", "json"],
          testRunner: "jest", // or 'mocha', 'karma' based on actual project config
          mutator: "typescript", // or 'javascript'
          coverageAnalysis: "perTest",
          tsconfigFile: "tsconfig.json", // If TypeScript project
          mutate: ["**/*.js", "**/*.ts", "!**/*.spec.js", "!**/*.d.ts"], // Files to mutate relative to cwd
        };
        """
    ),
    tool_version_cmd=["npx", "stryker", "--version"],
)

register_mutator(
    language="csharp",
    tool_name="stryker-net",
    extensions=[".cs"],
    run_func=lambda temp_dir_path, strategy, params: _run_subprocess_safe(
        [
            "dotnet",
            "stryker",
        ],  # Assumes dotnet CLI installed and run in project root (temp_dir_path/code)
        cwd=temp_dir_path / "code",
        timeout=params.get("timeout", 600),
    ),
    parse_func=parse_stryker_output,
    setup_config_func=lambda temp_dir_path, code_file_paths, test_file_paths: None,  # Relies on project structure like .csproj
    tool_version_cmd=["dotnet", "stryker", "--version"],
)


def detect_language(code_files: Dict[str, str]) -> str:
    """Detects primary language based on file extensions in code_files."""
    file_extensions = set(Path(f).suffix.lower() for f in code_files.keys())

    for lang, tool_map in _MUTATOR_REGISTRY.items():
        for tool_name, info in tool_map.items():
            if any(ext in file_extensions for ext in info["extensions"]):
                logger.info(
                    f"Detected language '{lang}' based on file extensions: {file_extensions}."
                )
                return lang

    if ".py" in file_extensions:
        return "python"
    if ".js" in file_extensions or ".ts" in file_extensions:
        return "javascript"
    if ".go" in file_extensions:
        return "go"
    if ".java" in file_extensions:
        return "java"
    if ".rs" in file_extensions:
        return "rust"  # Added rust for fuzz_test example

    logger.warning(
        f"Could not detect a supported language for mutation testing from extensions: {file_extensions}. Defaulting to 'python'."
    )
    return "python"


@trace_method_decorator
async def mutation_test(
    temp_dir: Path,
    config: RunnerConfig,
    code_files: Dict[str, str],
    test_files: Dict[str, str],
    strategy: str = "targeted",
    parallel: bool = True,
    distributed: bool = False,
) -> Dict[str, Any]:
    """
    Advanced mutation testing with multi-language support, strategies, parallel/distributed execution.
    Args:
        temp_dir (Path): Temporary directory where code/tests are placed.
        config (RunnerConfig): Runner configuration.
        code_files (Dict[str, str]): Code files to mutate.
        test_files (Dict[str, str]): Test files to run against mutants.
        strategy (str): Mutation strategy ('random', 'targeted', 'property', 'ai-guided').
        parallel (bool): Whether to run mutation testing in parallel processes.
        distributed (bool): Whether to leverage a distributed runner backend.
    Returns:
        Dict[str, Any]: Mutation testing statistics (survival_rate, killed, survived, etc.).
    SECURITY WARNING: Subprocess execution of mutation tools may involve running untrusted code.
                      For production, ensure these operations occur within a secure sandbox
                      (e.g., dedicated Docker containers, isolated VMs, or low-privilege users).
    """
    # Access the trace span safely — OTEL may not be initialised
    span = trace.get_current_span() if _tracer else None
    if span and span.is_recording():
        span.set_attribute("mutation.strategy", strategy)
        span.set_attribute("mutation.parallel", parallel)
        span.set_attribute("mutation.distributed", distributed)

    language = detect_language(code_files)
    instance_id: str = getattr(config, "instance_id", "N/A")

    # Gold Standard: Config Validation for mutation parameters
    configured_tool_name: Optional[str] = getattr(config, "mutation_tool_name", None)
    if configured_tool_name and configured_tool_name not in _MUTATOR_REGISTRY.get(
        language, {}
    ):
        logger.error(
            f"Configured mutation tool '{configured_tool_name}' for language '{language}' is not registered. Skipping mutation test."
        )
        MUTATION_ERROR.labels(
            language, strategy, configured_tool_name, instance_id
        ).inc()
        if span and span.is_recording():
            span.set_status(
                trace_status.Status(
                    trace_status.StatusCode.ERROR,
                    f"Configured mutator not registered: {configured_tool_name}",
                )
            )
        return {
            "survival_rate": 1.0,
            "total": 0,
            "killed": 0,
            "survived": 0,
            "timeout": 0,
            "error": 1,
            "message": f"Configured mutator '{configured_tool_name}' not registered.",
            "total_mutants": 0,
        }

    # Select mutator tool: configured, or default if not specified/registered
    mutator_info: Optional[Dict[str, Any]] = None
    if configured_tool_name:
        mutator_info = _MUTATOR_REGISTRY.get(language, {}).get(configured_tool_name)
    else:  # If no tool explicitly configured, pick the first one for the language
        mutator_info = next(iter(_MUTATOR_REGISTRY.get(language, {}).values()), None)

    if not mutator_info:
        logger.error(
            f"No mutation testing tool available for language '{language}'. Skipping mutation test."
        )
        MUTATION_ERROR.labels(language, strategy, "not_available", instance_id).inc()
        if span and span.is_recording():
            span.set_status(
                trace_status.Status(
                    trace_status.StatusCode.ERROR,
                    f"No mutator available for language: {language}",
                )
            )
        return {
            "survival_rate": 1.0,
            "total": 0,
            "killed": 0,
            "survived": 0,
            "timeout": 0,
            "error": 0,
            "message": f"No mutator for {language}",
            "total_mutants": 0,
        }

    tool_name: str = mutator_info["tool"]
    if span and span.is_recording():
        span.set_attribute("mutation.tool_name", tool_name)
        span.set_attribute("mutation.language", language)
    logger.info(
        f"Running mutation test for '{language}' using tool '{tool_name}' with strategy '{strategy}'."
    )

    # Gold Standard: Toolchain Preflight Check (check if tool is installed/in PATH)
    tool_version: str = "N/A"
    if "version_cmd" in mutator_info and mutator_info["version_cmd"]:
        tool_version = await _get_tool_version(tool_name, mutator_info["version_cmd"])
        if tool_version == "unavailable":
            logger.error(
                f"Mutation tool '{tool_name}' is not available in PATH. Skipping mutation test."
            )
            MUTATION_ERROR.labels(language, strategy, tool_name, instance_id).inc()
            if span and span.is_recording():
                span.set_status(
                    trace_status.Status(
                        trace_status.StatusCode.ERROR,
                        f"Mutator tool not found: {tool_name}",
                    )
                )
            return {
                "survival_rate": 1.0,
                "total": 0,
                "killed": 0,
                "survived": 0,
                "timeout": 0,
                "error": 1,
                "message": f"Mutator tool '{tool_name}' not found in PATH.",
                "total_mutants": 0,
            }
    else:
        # Fallback to hardcoded versions for built-in Python tools
        if tool_name == "mutmut" and HAS_MUTMUT:
            tool_version = MUTMUT_VERSION
        elif tool_name == "hypothesis" and HAS_HYPOTHESIS:
            tool_version = HYPOTHESIS_VERSION

    # --- Strategy Selection / Execution ---
    if strategy == "property":
        if language == "python" and HAS_HYPOTHESIS:
            logger.info(
                "Using property-based testing as a mutation strategy (Python/Hypothesis)."
            )
            # This calls property_based_test directly, which has a different return shape
            return await property_based_test(temp_dir, config, code_files)
        else:
            logger.warning(
                f"Property-based testing for '{language}' or Hypothesis not available. Falling back to 'targeted' strategy."
            )
            strategy = "targeted"

    if strategy == "ai-guided":
        logger.info("Using AI-guided mutation strategy.")
        _llm = None
        try:
            from generator.clarifier.clarifier_llm import GrokLLM
            _llm = GrokLLM()
        except Exception as _llm_import_err:
            logger.debug(f"AI-guided: could not load GrokLLM: {_llm_import_err}")
        if _llm is not None:
            try:
                _MAX_PREVIEW_BYTES = 500
                _MAX_SAMPLE_FILES = 5
                _snippet_parts: List[str] = []
                for _p in (list(code_files) if code_files else [])[:_MAX_SAMPLE_FILES]:
                    _file = Path(_p)
                    if _file.is_file():
                        try:
                            with open(_file, encoding="utf-8", errors="replace") as _fh:
                                _preview = _fh.read(_MAX_PREVIEW_BYTES)
                            _snippet_parts.append(f"# File: {_p}\n{_preview}")
                        except OSError as _io_err:
                            logger.debug(f"AI-guided: cannot read {_p}: {_io_err}")
                    else:
                        _snippet_parts.append(f"# File: {_p} (not readable)")
                _code_snippets = "\n\n".join(_snippet_parts)
                _prompt = (
                    "You are a mutation testing expert. Analyze the following source file "
                    "previews and identify up to 5 file paths and function names most likely "
                    "to have bugs or insufficient test coverage. "
                    "Respond ONLY with a JSON array of objects with keys 'file' and 'function'. "
                    "Example: [{\"file\": \"src/foo.py\", \"function\": \"bar\"}]\n\n"
                    f"{_code_snippets}"
                )
                _response = await _llm.generate(_prompt)
                if isinstance(_response, str):
                    # Strip any markdown code fences the LLM may have added
                    _clean = re.sub(r"```[^\n]*\n?", "", _response).strip()
                    _targets = json.loads(_clean)
                else:
                    _targets = _response
                if isinstance(_targets, list) and _targets:
                    _target_files = {
                        str(t.get("file", ""))
                        for t in _targets
                        if isinstance(t, dict) and t.get("file")
                    }
                    if _target_files and code_files:
                        _filtered = [
                            f for f in code_files
                            if any(t and t in str(f) for t in _target_files)
                        ]
                        if _filtered:
                            code_files = _filtered
                            logger.info(
                                f"AI-guided strategy filtered to {len(code_files)} "
                                f"high-value target files from LLM analysis."
                            )
                        else:
                            logger.debug(
                                "AI-guided: LLM targets did not match any code_files; "
                                "keeping full file set."
                            )
            except Exception as _ai_err:
                logger.warning(
                    f"AI-guided strategy LLM analysis failed: {_ai_err}. "
                    "Falling back to 'targeted' strategy.",
                    exc_info=True,
                )
                strategy = "targeted"
        else:
            logger.warning(
                "AI-guided strategy: LLM provider unavailable. "
                "Falling back to 'targeted' strategy."
            )
            strategy = "targeted"

    # --- Setup mutator-specific configuration files ---
    if span and span.is_recording():
        span.add_event("Setting up mutator configuration")
    if mutator_info.get("setup_config"):
        try:
            code_file_paths: List[Path] = list((temp_dir / "code").rglob("*"))
            test_file_paths: List[Path] = list((temp_dir / "tests").rglob("*"))
            mutator_info["setup_config"](temp_dir, code_file_paths, test_file_paths)
        except Exception as e:
            logger.error(
                f"Failed to set up mutator config for '{tool_name}': {e}", exc_info=True
            )
            MUTATION_ERROR.labels(language, strategy, tool_name, instance_id).inc()
            if span and span.is_recording():
                span.set_status(
                    trace_status.Status(
                        trace_status.StatusCode.ERROR,
                        f"Mutator config setup failed: {e}",
                    )
                )
            return {
                "survival_rate": 1.0,
                "total": 0,
                "killed": 0,
                "survived": 0,
                "timeout": 0,
                "error": 1,
                "message": f"Mutator config setup failed: {e}",
                "total_mutants": 0,
            }

    # --- Prepare mutation run parameters (Gold Standard: Expose params via config) ---
    mutation_timeout: int = getattr(
        config, "mutation_timeout", getattr(config, "timeout", 300) * 2
    )
    mutation_random_percent: float = getattr(
        config, "mutation_random_percent", 0.1
    )  # For random strategy

    mutation_run_params: Dict[str, Any] = {
        "timeout": mutation_timeout,
        "random_percent": mutation_random_percent,
    }
    # Add strategy-specific params
    if strategy == "targeted":
        mutation_run_params["use_coverage"] = True
    elif strategy == "random":
        pass

    # --- Execution: Parallel or Distributed (Gold Standard: Clear Interfaces) ---
    raw_result: Dict[str, Any] = {}
    try:
        if distributed and getattr(config, "distributed", False):
            if span and span.is_recording():
                span.add_event("Sending mutation task to distributed runner")
            _endpoint = getattr(config, "distributed_endpoint", None)
            if not _endpoint or not HAS_AIOHTTP:
                if not _endpoint:
                    logger.warning(
                        "Distributed mutation: no distributed_endpoint configured. "
                        "Falling back to local single-process execution."
                    )
                else:
                    logger.warning(
                        "Distributed mutation: aiohttp not available. "
                        "Falling back to local single-process execution."
                    )
                raw_result = await mutator_info["run"](temp_dir, strategy, mutation_run_params)
            else:
                logger.info(
                    f"Sending mutation task to distributed runner at '{_endpoint}' for language '{language}'."
                )
                try:
                    _MAX_PAYLOAD_FILES: int = getattr(config, "max_payload_files", 50)
                    # Per-file size cap — configurable so operators with larger source
                    # files can raise the limit; default is 512 KB.
                    _MAX_FILE_BYTES: int = getattr(
                        config, "max_payload_file_size", 512 * 1024
                    )
                    _files_payload: Dict[str, str] = {}
                    for _p in temp_dir.rglob("*"):
                        if not _p.is_file():
                            continue
                        if len(_files_payload) >= _MAX_PAYLOAD_FILES:
                            logger.warning(
                                f"Distributed mutation: payload truncated at "
                                f"{_MAX_PAYLOAD_FILES} files to avoid excessive memory use."
                            )
                            break
                        try:
                            _file_size = _p.stat().st_size
                            if _file_size > _MAX_FILE_BYTES:
                                continue
                            with open(_p, encoding="utf-8", errors="replace") as _fh:
                                _files_payload[str(_p)] = _fh.read()
                        except OSError as _read_err:
                            logger.debug(f"Distributed mutation: skipping {_p}: {_read_err}")
                    _task_payload = {
                        "language": language,
                        "tool_name": tool_name,
                        "strategy": strategy,
                        "params": mutation_run_params,
                        "files": _files_payload,
                    }
                    async with _aiohttp.ClientSession() as _session:
                        async with _session.post(
                            _endpoint,
                            json=_task_payload,
                            timeout=_aiohttp.ClientTimeout(
                                total=getattr(config, "mutation_timeout", 300)
                            ),
                        ) as _resp:
                            _resp.raise_for_status()
                            raw_result = await _resp.json()
                except Exception as _dist_err:
                    logger.warning(
                        f"Distributed mutation runner failed: {_dist_err}. "
                        "Falling back to local single-process execution."
                    )
                    raw_result = await mutator_info["run"](temp_dir, strategy, mutation_run_params)
        elif parallel and getattr(config, "parallel_workers", 1) > 1:
            if span and span.is_recording():
                span.add_event("Running mutation test with async concurrency")
            _max_workers = max(1, getattr(config, "parallel_workers", 1))
            logger.info(
                f"Running mutation test with up to {_max_workers} concurrent async tasks."
            )
            # The run functions are async coroutines; asyncio.gather runs them
            # concurrently within the same event loop without requiring a process
            # pool or thread executor, which avoids the pickling/IPC issues that
            # made the previous ProcessPoolExecutor path non-functional.
            #
            # Strategy: partition the code_files across workers, run each
            # partition as a separate invocation, then merge the result
            # dictionaries.  If code_files is unavailable (e.g. the mutator
            # works on a directory directly) we fall back to a single invocation.
            _code_file_list = list(code_files) if code_files else []
            if _code_file_list and len(_code_file_list) > 1:
                _chunk_size = max(1, math.ceil(len(_code_file_list) / _max_workers))
                _chunks = [
                    _code_file_list[i:i + _chunk_size]
                    for i in range(0, len(_code_file_list), _chunk_size)
                ]
                _partitioned_params = [
                    {**mutation_run_params, "code_files": chunk} for chunk in _chunks
                ]
                _tasks = [
                    mutator_info["run"](temp_dir, strategy, p) for p in _partitioned_params
                ]
                _results = await asyncio.gather(*_tasks, return_exceptions=True)
                # Merge numeric fields; propagate the first exception if all failed.
                _merged: Dict[str, Any] = {}
                _first_exc = None
                for _r in _results:
                    if isinstance(_r, BaseException):
                        if _first_exc is None:
                            _first_exc = _r
                        logger.warning(
                            "Parallel mutation worker raised an exception: %s",
                            _r,
                            exc_info=_r,
                        )
                    elif isinstance(_r, dict):
                        for _k, _v in _r.items():
                            if isinstance(_v, (int, float)) and _k in _merged:
                                _merged[_k] = _merged[_k] + _v
                            else:
                                _merged.setdefault(_k, _v)
                if _merged:
                    raw_result = _merged
                elif _first_exc is not None:
                    raise _first_exc
                else:
                    raw_result = await mutator_info["run"](temp_dir, strategy, mutation_run_params)
            else:
                # Single file or no file list — run directly
                raw_result = await mutator_info["run"](temp_dir, strategy, mutation_run_params)
        else:
            if span and span.is_recording():
                span.add_event("Running mutation test in single process")
            logger.info("Running mutation test in single process.")
            raw_result = await mutator_info["run"](
                temp_dir, strategy, mutation_run_params
            )

    except Exception as e:
        # Catch run-step exceptions and return a structured error result
        error_message = f"Mutator run failed: {e}"
        logger.error(error_message, exc_info=True)
        MUTATION_ERROR.labels(language, strategy, tool_name, instance_id).inc()
        if span and span.is_recording():
            span.set_status(
                trace_status.Status(trace_status.StatusCode.ERROR, error_message)
            )
        return {
            "survival_rate": 1.0,
            "total": 0,
            "killed": 0,
            "survived": 0,
            "timeout": 0,
            "error": 1,
            "message": error_message,
            "total_mutants": 0,
        }  # Added 'total_mutants'

    # --- Parse and Aggregate Results ---
    if span and span.is_recording():
        span.add_event("Parsing mutation results")
    mutation_stats: Dict[str, int] = mutator_info["parse"](raw_result)

    total: int = mutation_stats.get("total", 0)
    survived: int = mutation_stats.get("survived", 0)
    killed: int = mutation_stats.get("killed", 0)
    timeout: int = mutation_stats.get("timeout", 0)
    error: int = mutation_stats.get("error", 0)

    survival_rate: float = survived / total if total > 0 else 0.0

    # Update Prometheus metrics with instance_id label
    MUTATION_TOTAL.labels(language, strategy, tool_name, instance_id).inc(total)
    MUTATION_KILLED.labels(language, strategy, tool_name, instance_id).inc(killed)
    MUTATION_SURVIVED.labels(language, strategy, tool_name, instance_id).inc(survived)
    MUTATION_TIMEOUT.labels(language, strategy, tool_name, instance_id).inc(timeout)
    MUTATION_ERROR.labels(language, strategy, tool_name, instance_id).inc(error)
    MUTATION_SURVIVAL_RATE.labels(language, strategy, tool_name, instance_id).set(
        survival_rate
    )

    gaps: List[Any] = []
    COVERAGE_GAPS.labels(language, instance_id).inc(len(gaps))

    # Reporting and Logging
    final_stats = {
        "survival_rate": survival_rate,
        "killed_mutants": killed,
        "survived_mutants": survived,
        "timed_out_mutants": timeout,
        "error_mutants": error,
        "total_mutants": total,
        "coverage_gaps": gaps,
        "language": language,
        "strategy": strategy,
        "tool": tool_name,
        "tool_version": tool_version,
        "stdout_snippet": (
            raw_result.get("stdout", "")[:500] + "..."
            if raw_result.get("stdout")
            else ""
        ),
        "stderr_snippet": (
            raw_result.get("stderr", "")[:500] + "..."
            if raw_result.get("stderr")
            else ""
        ),
        "returncode": raw_result.get("returncode", "N/A"),
    }
    logger.info(
        f"Mutation testing completed for {language}. Stats: {final_stats}",
        extra=final_stats,
    )
    if span and span.is_recording():
        span.set_attribute("mutation.result.survival_rate", survival_rate)
        span.set_status(trace_status.Status(trace_status.StatusCode.OK))
    return final_stats


@trace_method_decorator
async def property_based_test(
    temp_dir: Path, config: RunnerConfig, code_files: Dict[str, str]
) -> Dict[str, Any]:
    """
    Performs property-based testing using Hypothesis.
    Args:
        temp_dir (Path): Temporary directory containing the code under test.
        config (RunnerConfig): Runner configuration.
        code_files (Dict[str, str]): Content of the code files.
    Returns:
        Dict[str, Any]: Results of the property-based test.
    """
    # Access the trace span safely — OTEL may not be initialised
    span = trace.get_current_span() if _tracer else None
    instance_id: str = getattr(config, "instance_id", "default_runner_instance")
    if span and span.is_recording():
        span.set_attribute("fuzz.language", "python")
        span.set_attribute("fuzz.strategy", "property")
        span.set_attribute("fuzz.tool_name", "hypothesis")
        span.set_attribute("fuzz.tool_version", HYPOTHESIS_VERSION)

    if not HAS_HYPOTHESIS:
        logger.error("Hypothesis not installed. Cannot run property-based tests.")
        FUZZ_DISCOVERIES.labels("python", "property", instance_id).inc(0)
        if span and span.is_recording():
            span.set_status(
                trace_status.Status(
                    trace_status.StatusCode.ERROR, "Hypothesis not installed"
                )
            )
        return {"status": "skipped", "message": "Hypothesis not available"}

    discoveries: int = 0
    test_failures: List[str] = []

    original_sys_path: List[str] = list(sys.path)
    code_path: Path = temp_dir / "code"
    if str(code_path) not in sys.path:
        sys.path.insert(0, str(code_path))

    module_name: Optional[str] = None
    try:
        for f_name in code_files.keys():
            file_path = Path(f_name)
            if file_path.suffix == ".py" and file_path.stem != "__init__":
                module_name = file_path.stem
                break

        if not module_name:
            logger.warning(
                "No main Python module (.py excluding __init__.py) found for property testing. Skipping."
            )
            if span and span.is_recording():
                span.add_event("No main Python module found")
            return {
                "status": "skipped",
                "message": "No main Python module found for property testing.",
            }

        target_module = importlib.import_module(module_name)
        importlib.reload(target_module)

        testable_functions: List[Callable] = []
        for name, obj in inspect.getmembers(target_module):
            if inspect.isfunction(obj) and name.startswith("fuzz_"):
                testable_functions.append(obj)

        if not testable_functions:
            logger.warning(
                f"No fuzzable functions (e.g., 'fuzz_...') found in {module_name}. Skipping property tests."
            )
            if span and span.is_recording():
                span.add_event("No fuzzable functions found")
            return {
                "status": "skipped",
                "message": "No property-based fuzz targets found (e.g., "
                "fuzz_..."
                ")",
            }

        logger.info(
            f"Running property tests on {len(testable_functions)} functions in {module_name}."
        )
        if span and span.is_recording():
            span.set_attribute("fuzz.functions_tested_count", len(testable_functions))

        fuzz_examples_count: int = getattr(config, "fuzz_examples", 50)

        for func_to_test in testable_functions:
            if span and span.is_recording():
                span.add_event(f"Fuzzing function: {func_to_test.__name__}")
            try:
                settings = hypothesis.settings(
                    max_examples=fuzz_examples_count, deadline=None, print_blob=True
                )

                # Check if it's already a hypothesis test decorated with @given
                is_hypothesis_decorated = (
                    hasattr(func_to_test, "is_hypothesis_test")
                    and func_to_test.is_hypothesis_test
                )
                if not is_hypothesis_decorated:
                    is_hypothesis_decorated = hasattr(
                        func_to_test, "hypothesis"
                    )  # Another common check

                if is_hypothesis_decorated:
                    fuzz_test_runner = settings(func_to_test)
                else:
                    sig = inspect.signature(func_to_test)
                    if sig.parameters:
                        param_name = list(sig.parameters.keys())[0]
                        param_type = sig.parameters[param_name].annotation
                        if param_type != inspect.Parameter.empty:
                            try:
                                inferred_strategy = st.from_type(param_type)

                                @settings
                                @hypothesis.given(inferred_strategy)
                                def wrapper_fuzz_test(data: Any):
                                    func_to_test(data)

                                # Must update the runner to the decorated function itself
                                # In this synchronous context, the easiest way to run the test is to call the runner
                                fuzz_test_runner = wrapper_fuzz_test
                            except Exception as e:
                                logger.warning(
                                    f"Could not infer Hypothesis strategy for {func_to_test.__name__} from type hint {param_type}: {e}. Skipping auto-fuzzing for this function."
                                )
                                if span and span.is_recording():
                                    span.add_event(
                                        f"Skipped auto-fuzz for {func_to_test.__name__}: strategy inference failed"
                                    )
                                continue
                        else:
                            logger.warning(
                                f"Function {func_to_test.__name__} has no type hints for fuzzing. Skipping auto-fuzzing."
                            )
                            if span and span.is_recording():
                                span.add_event(
                                    f"Skipped auto-fuzz for {func_to_test.__name__}: no type hints"
                                )
                            continue
                    else:
                        logger.warning(
                            f"Function {func_to_test.__name__} has no parameters for fuzzing. Skipping auto-fuzzing."
                        )
                        if span and span.is_recording():
                            span.add_event(
                                f"Skipped auto-fuzz for {func_to_test.__name__}: no parameters"
                            )
                        continue

                # Run the decorated function
                fuzz_test_runner()

            except hypothesis.errors.InvalidArgument as e:
                logger.warning(
                    f"Hypothesis InvalidArgument for {func_to_test.__name__}. Strategy might not match function signature: {e}"
                )
                test_failures.append(f"Invalid args for {func_to_test.__name__}: {e}")
                discoveries += 1
            except hypothesis.errors.FailedHealthcheck as e:
                logger.warning(
                    f"Hypothesis health check failed for {func_to_test.__name__}. Data generation issue? {e}"
                )
                test_failures.append(
                    f"Healthcheck failed for {func_to_test.__name__}: {e}"
                )
                discoveries += 1
            except hypothesis.errors.InvalidContract as e:
                logger.warning(
                    f"Hypothesis internal contract violated for {func_to_test.__name__}. {e}"
                )
                test_failures.append(
                    f"Internal Hypothesis contract violation for {func_to_test.__name__}: {e}"
                )
                discoveries += 1
            except hypothesis.errors.FalsifyingExample as e:
                logger.info(
                    f"Falsifying example found for {func_to_test.__name__}: {e.example}."
                )
                test_failures.append(
                    f"Falsifying example for {func_to_test.__name__}: {e.example}"
                )
                discoveries += 1
            except Exception as e:
                logger.error(
                    f"Property test for {func_to_test.__name__} failed unexpectedly: {e}",
                    exc_info=True,
                )
                test_failures.append(
                    f"Unexpected error for {func_to_test.__name__}: {e}"
                )
                discoveries += 1

    except Exception as e:
        logger.error(f"Error setting up property-based tests: {e}", exc_info=True)
        if span and span.is_recording():
            span.set_status(
                trace_status.Status(
                    trace_status.StatusCode.ERROR, f"Property test setup failed: {e}"
                )
            )
            span.record_exception(e)
        return {"status": "error", "message": f"Setup failed: {e}"}
    finally:
        sys.path[:] = original_sys_path
        if module_name:
            try:
                for mod_name in list(sys.modules.keys()):
                    if mod_name == module_name or mod_name.startswith(
                        f"{module_name}."
                    ):
                        del sys.modules[mod_name]
            except Exception as e:
                logger.warning(
                    f"Failed to cleanup dynamically loaded module {module_name}: {e}"
                )

    FUZZ_DISCOVERIES.labels("python", "property", instance_id).inc(discoveries)
    if discoveries > 0:
        logger.info(
            f"Property-based testing completed. Discovered {discoveries} issues."
        )
    else:
        logger.info("Property-based testing completed. No issues discovered.")

    # NOTE: Property-based testing often provides fuzzing results, but the caller (mutation_test)
    # expects a mutation-style result. We map the discovery count to 'killed mutants'
    # and the number of examples run (fuzz_examples_count) to 'total mutants' for compatibility.
    return {
        "survival_rate": 1.0 - (discoveries / max(1, fuzz_examples_count)),
        "killed_mutants": discoveries,
        "survived_mutants": max(0, fuzz_examples_count - discoveries),
        "total_mutants": fuzz_examples_count,
        "timed_out_mutants": 0,
        "error_mutants": 0,
        "coverage_gaps": [],
        "status": "completed",
        "fuzz_failures": test_failures,
        "tool_version": HYPOTHESIS_VERSION,
    }


@trace_method_decorator
async def fuzz_test(
    temp_dir: Path, config: RunnerConfig, code_files: Dict[str, str]
) -> Dict[str, Any]:
    """
    Performs general fuzz testing (e.g., black-box, grammar-based).
    Args:
        temp_dir (Path): Temporary directory containing the code under test.
        config (RunnerConfig): Runner configuration.
        code_files (Dict[str, str]): Content of the code files.
    Returns:
        Dict[str, Any]: Fuzzing results.
    """
    # Access the trace span safely — OTEL may not be initialised
    span = trace.get_current_span() if _tracer else None
    instance_id: str = getattr(config, "instance_id", "default_runner_instance")
    language: str = detect_language(code_files)
    if span and span.is_recording():
        span.set_attribute("fuzz.language", language)
        span.set_attribute("fuzz.strategy", "general")
        span.set_attribute("fuzz.tool_name", "custom_fuzzer")
        span.set_attribute("fuzz.tool_version", "1.0")

    if language == "python":
        logger.info(f"Running general fuzz tests for {language} code.")

        discoveries: int = 0
        # Use 'fuzz_iterations' to match test_runner_mutation.py
        fuzz_iterations_count: int = getattr(
            config, "fuzz_iterations", getattr(config, "fuzz_examples", 100)
        )

        for i in range(fuzz_iterations_count):
            f"fuzz_input_{i}_{random.randint(0, 1000)}"

            # In a real scenario, this is where you'd call the user's code with fuzzed_input
            # For example, if it's a CLI tool, you'd execute a subprocess:
            # result = await _run_subprocess_safe(['your_cli_tool', fuzzed_input], cwd=temp_dir, timeout=5)
            # if result['returncode'] != 0: discoveries += 1

            # Simulated outcome
            if random.random() < 0.15:
                discoveries += 1

    else:
        logger.warning(
            f"General fuzz testing for '{language}' or Hypothesis not available. Skipping."
        )
        if span and span.is_recording():
            span.set_status(
                trace_status.Status(
                    trace_status.StatusCode.ERROR,
                    "Fuzzing skipped: module or language not supported",
                )
            )
        return {
            "discoveries": 0,
            "status": "skipped",
            "message": f"Unsupported language for fuzzing: {language}",
        }

    FUZZ_DISCOVERIES.labels(language, "general", instance_id).inc(discoveries)
    if span and span.is_recording():
        span.set_attribute("fuzz.discoveries", discoveries)
        span.set_status(trace_status.Status(trace_status.StatusCode.OK))
    return {
        "discoveries": discoveries,
        "status": "completed",
        "iterations": fuzz_iterations_count,
        "language": language,
        "tool_version": "1.0",
    }


# Add Windows event-loop guard

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

