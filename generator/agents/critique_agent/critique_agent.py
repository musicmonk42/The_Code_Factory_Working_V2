import asyncio
import hashlib
import inspect
import json
import logging
import os
import random
import re
import shlex
import shutil
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from dotenv import load_dotenv
from opentelemetry import metrics, trace
from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from pydantic import BaseModel, Field, ValidationError, model_validator

# --- CORE RUNNER & SHARED UTILITY IMPORTS (ENFORCED) ---
try:
    # Central Utilities from Runner
    from runner.llm_client import call_ensemble_api, call_llm_api  # Unified LLM Clients
    from runner.runner_core import (
        run_tests as runner_run_tests,  # Central test runner for safety checks
    )
    from runner.runner_file_utils import (
        save_files_to_output,
    )  # Use canonical file saver
    # FIX: Import from runner_audit to avoid circular dependency
    from runner.runner_audit import log_audit_event
    from runner.runner_security_utils import (  # Central security scan utility
        scan_for_vulnerabilities,
    )

    from .critique_fixer import apply_auto_fixes
    from .critique_linter import run_all_lints_and_checks

    # Imports from Sibling Critique Modules
    from .critique_prompt import build_semantic_critique_prompt

except ImportError as e:
    # Fallback stubs for degraded / test environments.
    logging.critical(f"CRITIQUE AGENT FAILED TO LOAD RUNNER DEPENDENCIES: {e}")

    async def log_audit_event(*args, **kwargs) -> None:
        logging.warning("Audit logging disabled.")

    def scan_for_vulnerabilities(*args, **kwargs):
        return []

    async def call_llm_api(*args, **kwargs):
        raise NotImplementedError("LLM API unavailable")

    async def call_ensemble_api(*args, **kwargs):
        raise NotImplementedError("LLM API unavailable")

    async def runner_run_tests(*args, **kwargs):
        return {"pass_rate": 0.0, "coverage_percentage": 0.0}

    async def build_semantic_critique_prompt(*args, **kwargs):
        return "CRITIQUE_PROMPT_UNAVAILABLE"

    async def run_all_lints_and_checks(*args, **kwargs):
        return {"all_errors": []}

    async def apply_auto_fixes(*args, **kwargs):
        if args and isinstance(args[0], dict):
            return args[0]
        return {}

    async def save_files_to_output(files: Dict[str, str], output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in files.items():
            file_path = output_dir / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                file_path.write_text(content, encoding="utf-8")
            except Exception as e:
                logging.error(f"Fallback save_files_to_output failed: {e}")


# Import omnicore plugin decorator
try:
    from omnicore_engine.plugin_registry import PlugInKind, plugin
except ImportError:

    def plugin(**kwargs):
        def decorator(func):
            return func

        return decorator

    class PlugInKind:
        CHECK = "CHECK"
        FIX = "FIX"


load_dotenv()

# Structured JSON Logging Setup
logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '{"time": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --- OpenTelemetry and Prometheus Metrics Setup ---
# Use the default/configured tracer provider instead of manually creating one
# This avoids version compatibility issues and respects OTEL_* environment variables
try:
    tracer = trace.get_tracer(__name__)
except TypeError:
    # Fallback for older OpenTelemetry versions
    tracer = None
meter = metrics.get_meter(__name__)


# --- Enterprise-Grade Metric Registration with Deduplication Protection ---
#
# Industry Standard Compliance:
# - SOC 2 Type II: Reliable metric collection without service disruption
# - ISO 27001 A.12.1.3: Capacity management through proper observability
#
# Design Pattern: Check-before-create to prevent ValueError on duplicate registration


def _get_or_create_metric(metric_class, name: str, description: str, labelnames=None):
    """
    Enterprise-grade metric factory with idempotent registration.

    Implements check-before-create pattern to prevent 'Duplicated timeseries
    in CollectorRegistry' errors that crash agents during initialization.

    Args:
        metric_class: prometheus_client metric class (Counter, Gauge, Histogram)
        name: Unique metric name following prometheus naming conventions
        description: Human-readable metric description
        labelnames: Optional list of label names for dimensional metrics

    Returns:
        Existing or newly created metric instance
    """
    labelnames = labelnames or []

    # Check if metric already exists in registry (idempotent)
    try:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing
    except (AttributeError, KeyError):
        pass

    # Create new metric if it doesn't exist
    try:
        if labelnames:
            return metric_class(name, description, labelnames)
        return metric_class(name, description)
    except ValueError as e:
        # Handle race condition: metric was created by another thread/process
        if "Duplicated timeseries" in str(e):
            existing = REGISTRY._names_to_collectors.get(name)
            if existing is not None:
                return existing
        raise


CRITIQUE_STEPS = _get_or_create_metric(
    Counter,
    "critique_steps_total",
    "Total critique steps",
    ["step"],
)
CRITIQUE_LATENCY = _get_or_create_metric(
    Histogram,
    "critique_latency_seconds",
    "Critique step latency",
    ["step"],
)
CRITIQUE_ERRORS = _get_or_create_metric(
    Counter,
    "critique_errors_total",
    "Critique errors",
    ["step", "error_type", "tool"],
)
CRITIQUE_COVERAGE = _get_or_create_metric(
    Gauge,
    "critique_coverage",
    "Test coverage percentage",
)
CRITIQUE_VULNERABILITIES_FOUND = _get_or_create_metric(
    Counter,
    "critique_vulnerabilities_found_total",
    "Total vulnerabilities found",
    ["tool", "severity"],
)


# Audit Logger wrapper
class JsonConsoleAuditLogger:
    async def log_action(self, *args, **kwargs) -> None:
        await log_audit_event(*args, **kwargs)


_audit_logger = JsonConsoleAuditLogger()


async def log_action(*args, **kwargs) -> None:
    """Async wrapper for audit logging."""
    await _audit_logger.log_action(*args, **kwargs)


# --- Production-Ready Configuration Schema ---
class CritiqueConfig(BaseModel):
    """
    Central configuration for the critique pipeline.

    Tests expect:
    - Sensible defaults.
    - Invalid / nonsense values either rejected or normalized.
    """

    languages: List[str] = ["python", "javascript", "go"]
    target_language: str = "auto"  # 'auto' => detect from code_files
    pipeline_steps: List[str] = Field(
        default_factory=lambda: [
            "lint",
            "test",
            "e2e_test",
            "stress_test",
            "security_scan",
            "semantic",
            "fix",
        ]
    )

    hitl_callback: Optional[Callable[[Dict[str, Any]], bool]] = None

    max_retries: int = 3
    self_heal_threshold: float = 0.5
    explainability: bool = True

    enable_e2e_tests: bool = True
    enable_stress_tests: bool = True
    enable_chaos_injection: bool = False

    enable_vulnerability_scan: bool = True
    vulnerability_scan_tools: Dict[str, List[str]] = Field(
        default_factory=lambda: {
            "python": ["bandit", "semgrep", "snyk_code"],
            "javascript": ["npm_audit", "retirejs", "semgrep"],
            "go": ["govulncheck", "gosec", "semgrep"],
        }
    )

    tool_timeout_seconds: int = 300

    enable_containerization: bool = True
    container_image_prefix: str = "critique-tool-"

    vulnerability_suppression_rules: Dict[str, Any] = Field(
        default_factory=lambda: {
            "ignore_severities": ["INFO"],
            "ignore_rules": [],
        }
    )

    # Expected by tests: must exist and be >= 1 (or normalized)
    max_parallel_steps: int = 4

    @model_validator(mode="before")
    @classmethod
    def _normalize_and_validate(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enforce the "no silent footguns" contract used by tests:

        - languages:
            - Drop unknown languages.
            - If all are invalid -> raise.
        - pipeline_steps:
            - Drop unknown steps.
            - If all steps are invalid/removed -> raise.
        - max_retries:
            - Negative values normalized to 0.
        - max_parallel_steps:
            - Must be int; <1 normalized to 1.
        """
        # Sanitize languages
        allowed_langs = {"python", "javascript", "go"}
        langs = values.get("languages") or []
        if not isinstance(langs, list):
            raise ValueError("languages must be a list of strings")
        sanitized_langs = [
            lang for lang in langs if isinstance(lang, str) and lang in allowed_langs
        ]

        if langs and not sanitized_langs:
            # Only invalid languages were provided: fail fast.
            raise ValueError(
                "No valid languages specified in CritiqueConfig.languages."
            )
        if sanitized_langs:
            values["languages"] = sanitized_langs
        else:
            # Ensure we always have at least one valid language
            values["languages"] = ["python"]

        # Sanitize pipeline_steps
        allowed_steps = {
            "lint",
            "test",
            "e2e_test",
            "stress_test",
            "security_scan",
            "semantic",
            "fix",
        }
        steps = values.get("pipeline_steps") or []
        if not isinstance(steps, list):
            raise ValueError("pipeline_steps must be a list of strings")
        sanitized_steps = [
            s for s in steps if isinstance(s, str) and s in allowed_steps
        ]

        if steps and not sanitized_steps:
            # Caller attempted only invalid steps: hard fail.
            raise ValueError("No valid pipeline_steps specified.")
        if sanitized_steps:
            values["pipeline_steps"] = sanitized_steps
        # If no pipeline_steps provided, let the default_factory handle it.
        # Default factory (defined at line 233-242) provides:
        #   ["lint", "test", "e2e_test", "stress_test", "security_scan", "semantic", "fix"]
        # Don't set a fallback here as it would override the default_factory

        # Normalize max_retries
        max_retries = values.get("max_retries", 3)
        if not isinstance(max_retries, int):
            raise ValueError("max_retries must be an integer")
        if max_retries < 0:
            values["max_retries"] = 0

        # Normalize max_parallel_steps
        mps = values.get("max_parallel_steps", 4)
        if not isinstance(mps, int):
            raise ValueError("max_parallel_steps must be an integer")
        if mps < 1:
            values["max_parallel_steps"] = 1

        return values


# --- LLM Wrapper Function (Centralized Call) ---
async def call_llm_for_critique(
    prompt: str,
    step_name: str,
    config: CritiqueConfig,
) -> Dict[str, Any]:
    """
    Wrapper to call the unified LLM client for critique/semantic steps.

    Expects the underlying LLM (via call_llm_api) to return JSON (or markdown-wrapped JSON),
    but degrades gracefully when it doesn't.
    """
    response = await call_llm_api(prompt=prompt, provider=config.target_language)

    content = response.get("content") if isinstance(response, dict) else str(response)

    if content:
        try:
            # Try ```json ... ``` fenced block first
            json_match = re.search(
                r"```json\s*(.*?)\s*```",
                content,
                re.DOTALL | re.IGNORECASE,
            )
            if json_match:
                parsed_content = json.loads(json_match.group(1))
            else:
                parsed_content = json.loads(content)

            if isinstance(response, dict):
                # Merge decoded JSON into original response
                response.update(parsed_content)
                return response
            else:
                return parsed_content
        except json.JSONDecodeError:
            logger.warning(
                f"LLM response not valid JSON for {step_name}. Returning raw content."
            )
            return {
                "content": content,
                "error": "LLM output was not valid JSON.",
            }

    return {
        "content": content or "No content returned.",
        "error": "LLM returned empty response.",
    }


# --- Helper Functions ---
def check_owasp_compliance(code_files: Dict[str, str]) -> List[Dict[str, Any]]:
    """
    Simple internal OWASP-ish heuristic checks.
    Kept lightweight; real scanning is done via scan_for_vulnerabilities.
    """
    issues: List[Dict[str, Any]] = []
    for file, content in code_files.items():
        if re.search(r"\beval\s*\(", content):
            issues.append(
                {
                    "file": file,
                    "issue": "A1: Injection - eval detected",
                    "severity": "HIGH",
                }
            )
        if re.search(r"sql\s*=\s*[\'\"].*?%s", content, re.IGNORECASE):
            issues.append(
                {
                    "file": file,
                    "issue": "A1: Injection - potential SQL injection",
                    "severity": "HIGH",
                }
            )
    return issues


# --- Abstract Base Class for Language Plugins ---
class LanguageCritiquePlugin(ABC):
    """
    Authoritative definition of language-specific critique plugins.
    """

    def __init__(self, config: CritiqueConfig):
        self.config = config

    @abstractmethod
    async def lint(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def run_unit_tests(
        self,
        test_files: Dict[str, str],
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def run_e2e_tests(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def run_stress_tests(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]: ...

    @abstractmethod
    async def vulnerability_scan(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        tools: List[str],
        timeout: int,
        config: CritiqueConfig,
    ) -> Dict[str, Any]: ...

    async def _run_tool(
        self,
        command: List[str],
        cwd: Path,
        tool_name: str,
        timeout: int,
        use_container: bool = False,
        container_image: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Local/Container tool execution helper.

        In a fully wired environment this should be replaced by a shared
        subprocess/runner wrapper; we keep it here for compatibility.
        """
        if use_container:
            if not shutil.which("docker"):
                logger.error("Docker not installed.")
                CRITIQUE_ERRORS.labels(
                    "tool_execution",
                    "docker_not_found",
                    tool_name,
                ).inc()
                return False, {"error": "Docker not found."}
            if not container_image:
                logger.error(f"No container image for {tool_name}.")
                CRITIQUE_ERRORS.labels(
                    "tool_execution",
                    "no_container_image",
                    tool_name,
                ).inc()
                return False, {"error": f"No container image specified for {tool_name}"}

            pull_success, _ = await self._run_tool(
                ["docker", "pull", container_image],
                Path.cwd(),
                "docker_pull",
                60,
            )
            if not pull_success:
                return (
                    False,
                    {
                        "error": f"Failed to pull Docker image {container_image}",
                    },
                )

            abs_cwd = cwd.resolve()
            container_command = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{shlex.quote(str(abs_cwd))}:/app",
                "-w",
                "/app",
                container_image,
            ] + command
            logger.info(
                f"Executing {tool_name} in container: "
                f"`{' '.join(shlex.quote(arg) for arg in container_command)}`"
            )
            full_command = container_command
        else:
            if not shutil.which(command[0]):
                logger.error(f"Tool '{command[0]}' not found.")
                CRITIQUE_ERRORS.labels(
                    "tool_execution",
                    "tool_not_found_in_path",
                    tool_name,
                ).inc()
                return False, {"error": f"Tool '{command[0]}' not found."}
            logger.info(
                f"Executing {tool_name}: "
                f"`{' '.join(shlex.quote(arg) for arg in command)}` in `{cwd}`"
            )
            full_command = command

        try:
            proc = await asyncio.create_subprocess_exec(
                *full_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd if not use_container else None,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            stdout_dec = stdout.decode(errors="ignore")
            stderr_dec = stderr.decode(errors="ignore").strip()

            if proc.returncode != 0:
                logger.error(f"{tool_name} failed: {stderr_dec}")
                CRITIQUE_ERRORS.labels(
                    "tool_execution",
                    f"{tool_name}_execution_error",
                    tool_name,
                ).inc()
                return (
                    False,
                    {
                        "error": f"{tool_name} execution failed",
                        "details": stderr_dec,
                        "raw_output": stdout_dec,
                    },
                )

            # Try parse JSON object from output slice
            json_start = stdout_dec.find("{")
            json_end = stdout_dec.rfind("}") + 1
            if json_start != -1 and json_end > json_start:
                try:
                    return True, json.loads(stdout_dec[json_start:json_end])
                except json.JSONDecodeError:
                    pass

            # Try line-delimited JSON
            lines = [line.strip() for line in stdout_dec.splitlines() if line.strip()]
            if lines and all(
                line.startswith("{") and line.endswith("}") for line in lines
            ):
                parsed_lines: List[Any] = []
                for line in lines:
                    try:
                        parsed_lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning(
                            f"Skipping malformed JSON line from {tool_name}: {line}"
                        )
                if parsed_lines:
                    return True, {
                        "line_delimited_json_results": parsed_lines,
                    }

            logger.debug(f"{tool_name} produced no valid JSON; returning raw output.")
            return True, {"raw_output": stdout_dec}
        except FileNotFoundError:
            logger.error(f"Tool '{full_command[0]}' not found.")
            CRITIQUE_ERRORS.labels(
                "tool_execution",
                "tool_not_found",
                tool_name,
            ).inc()
            return False, {"error": f"Tool not found: {tool_name}"}
        except asyncio.TimeoutError:
            logger.error(f"{tool_name} timed out after {timeout} seconds.")
            CRITIQUE_ERRORS.labels(
                "tool_execution",
                "timeout_error",
                tool_name,
            ).inc()
            return False, {"error": "Scan timed out"}
        except Exception as e:
            logger.error(
                f"Unexpected error in {tool_name}: {e}",
                exc_info=True,
            )
            CRITIQUE_ERRORS.labels(
                "tool_execution",
                "unexpected_error",
                tool_name,
            ).inc()
            return False, {"error": f"Unexpected error: {str(e)}"}


# --- Python Plugin ---
class PythonCritiquePlugin(LanguageCritiquePlugin):
    async def lint(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        logger.info(f"Running Python lint for files in {temp_dir}")
        await save_files_to_output(code_files, temp_dir)
        # Delegate to shared lints/checks
        return await run_all_lints_and_checks(
            code_files,
            str(temp_dir),
            language="python",
        )

    async def run_unit_tests(
        self,
        test_files: Dict[str, str],
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        payload = {
            "test_files": test_files,
            "code_files": code_files,
            "output_path": str(temp_dir),
            "config": {
                "language": "python",
                "timeout": config.tool_timeout_seconds,
            },
        }
        return await runner_run_tests(payload)

    async def run_e2e_tests(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        return {
            "status": "skipped",
            "reason": "E2E tests not implemented for Python critique.",
        }

    async def run_stress_tests(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        return {
            "status": "skipped",
            "reason": "Stress tests not implemented for Python critique.",
        }

    async def vulnerability_scan(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        tools: List[str],
        timeout: int,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        # scan_for_vulnerabilities only accepts target and scan_type
        return await scan_for_vulnerabilities(
            str(temp_dir),
            scan_type="code"
        )


# --- JavaScript Plugin ---
class JavaScriptCritiquePlugin(LanguageCritiquePlugin):
    async def lint(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        logger.info("Running JavaScript lint (ESLint)...")
        await save_files_to_output(code_files, temp_dir)
        return await run_all_lints_and_checks(
            code_files,
            str(temp_dir),
            lang="javascript",
        )

    async def run_unit_tests(
        self,
        test_files: Dict[str, str],
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        payload = {
            "test_files": test_files,
            "code_files": code_files,
            "output_path": str(temp_dir),
            "config": {
                "language": "javascript",
                "timeout": config.tool_timeout_seconds,
            },
        }
        return await runner_run_tests(payload)

    async def run_e2e_tests(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        return {
            "status": "skipped",
            "reason": "E2E tests not implemented for JavaScript critique.",
        }

    async def run_stress_tests(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        return {
            "status": "skipped",
            "reason": "Stress tests not implemented for JavaScript critique.",
        }

    async def vulnerability_scan(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        tools: List[str],
        timeout: int,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        # scan_for_vulnerabilities only accepts target and scan_type
        return await scan_for_vulnerabilities(
            str(temp_dir),
            scan_type="code"
        )


# --- Go Plugin ---
class GoCritiquePlugin(LanguageCritiquePlugin):
    async def lint(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        logger.info("Running Go lint (golangci-lint)...")
        await save_files_to_output(code_files, temp_dir)
        return await run_all_lints_and_checks(
            code_files,
            str(temp_dir),
            lang="go",
        )

    async def run_unit_tests(
        self,
        test_files: Dict[str, str],
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        payload = {
            "test_files": test_files,
            "code_files": code_files,
            "output_path": str(temp_dir),
            "config": {
                "language": "go",
                "timeout": config.tool_timeout_seconds,
            },
        }
        return await runner_run_tests(payload)

    async def run_e2e_tests(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        return {
            "status": "skipped",
            "reason": "E2E tests not implemented for Go critique.",
        }

    async def run_stress_tests(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        return {
            "status": "skipped",
            "reason": "Stress tests not implemented for Go critique.",
        }

    async def vulnerability_scan(
        self,
        code_files: Dict[str, str],
        temp_dir: Path,
        tools: List[str],
        timeout: int,
        config: CritiqueConfig,
    ) -> Dict[str, Any]:
        # scan_for_vulnerabilities only accepts target and scan_type
        return await scan_for_vulnerabilities(
            str(temp_dir),
            scan_type="code"
        )


# --- Dynamic Plugin Registry ---
_PLUGINS: Dict[str, Type[LanguageCritiquePlugin]] = {}


def register_plugin(name: str, plugin_cls: Type[LanguageCritiquePlugin]) -> None:
    if not issubclass(plugin_cls, LanguageCritiquePlugin):
        raise TypeError("Plugin must be a subclass of LanguageCritiquePlugin.")
    logger.info(f"Registering critique plugin class for language: {name}")
    _PLUGINS[name] = plugin_cls


def get_plugin(language: str, config: CritiqueConfig) -> LanguageCritiquePlugin:
    lang = (language or "python").lower()
    plugin_cls = _PLUGINS.get(lang)
    if not plugin_cls:
        logger.error(
            f"No critique plugin for {lang}. Available: {list(_PLUGINS.keys())}"
        )
        raise ValueError(f"Unsupported language: {lang}")
    return plugin_cls(config)


# Register core plugins
register_plugin("python", PythonCritiquePlugin)
register_plugin("javascript", JavaScriptCritiquePlugin)
register_plugin("go", GoCritiquePlugin)


def detect_language(code_files: Dict[str, str]) -> str:
    lang_counts: Dict[str, int] = {}
    for filename in code_files.keys():
        if filename.endswith((".py", ".pyc")):
            lang_counts["python"] = lang_counts.get("python", 0) + 1
        elif filename.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
            lang_counts["javascript"] = lang_counts.get("javascript", 0) + 1
        elif filename.endswith(".go"):
            lang_counts["go"] = lang_counts.get("go", 0) + 1

    if not lang_counts:
        logger.warning("No recognizable language extensions; defaulting to 'python'.")
        return "python"

    most_common_lang = max(lang_counts, key=lang_counts.get)
    if most_common_lang in _PLUGINS:
        logger.info(f"Detected primary language: {most_common_lang}")
        return most_common_lang

    logger.warning(
        f"Detected '{most_common_lang}' but no plugin registered; "
        f"defaulting to 'python'."
    )
    return "python"


def generate_provenance_id() -> str:
    return (
        f"{uuid.uuid4().hex}-"
        f"{hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:12]}"
    )


async def resilient_step(
    func: Callable,
    *args: Any,
    step_name: str,
    config: CritiqueConfig,
    **kwargs: Any,
) -> Any:
    """
    Resilient wrapper: retries, chaos, metrics, and HITL/self-heal hooks.

    NOTE: Tests monkeypatch this symbol; keep the call signature stable.
    """
    provenance_id = generate_provenance_id()

    attempts = config.max_retries if config.max_retries >= 1 else 1

    for attempt in range(1, attempts + 1):
        with tracer.start_as_current_span(
            step_name,
            attributes={
                "attempt": attempt,
                "provenance_id": provenance_id,
            },
        ):
            start_time = time.monotonic()
            try:
                chaos_injection(step_name, config)

                # --- FIX START ---
                # Inject 'config' and 'step_name' if the wrapped function's signature accepts them
                sig = inspect.signature(func)

                # 1. Handle 'config' argument
                if "config" in sig.parameters:
                    kwargs["config"] = config
                # If function *doesn't* want 'config', pop it if it was passed by mistake.
                elif "config" in kwargs:
                    kwargs.pop("config", None)

                # 2. Handle 'step_name' argument (this is a separate, independent check)
                if "step_name" in sig.parameters:
                    kwargs["step_name"] = step_name
                # --- FIX END ---

                result = await func(*args, **kwargs)

                if config.hitl_callback:
                    hitl_context = {
                        "step_name": step_name,
                        "result": result,
                        "code_files": kwargs.get("code_files", {}),
                    }
                    if not config.hitl_callback(hitl_context):
                        raise ValueError("HITL rejected; triggering re-evaluation.")

                score = (
                    result.get("semantic_alignment_score", 1.0)
                    if isinstance(result, dict) and step_name == "semantic"
                    else 1.0
                )
                if score < config.self_heal_threshold:
                    kwargs["extra_context"] = result.get(
                        "feedback_for_self_heal",
                        "Improve based on prior low score.",
                    )
                    raise RuntimeError(f"Triggering self-healing retry: {score}")

                await log_action(
                    "step_success",
                    {
                        "step": step_name,
                        "provenance_id": provenance_id,
                        "result": result,
                    },
                )
                return result
            except (ValueError, RuntimeError) as e:
                logger.warning(f"{step_name} (attempt {attempt}): {e}")
                if attempt == attempts:
                    logger.error(f"{step_name} failed after {attempts} attempts.")
                    CRITIQUE_ERRORS.labels(
                        step_name,
                        type(e).__name__,
                        "self_heal_failure",
                    ).inc()
                    return {
                        "error": str(e),
                        "provenance_id": provenance_id,
                    }
                await asyncio.sleep(1 * (2 ** (attempt - 1)))
            except Exception as e:
                CRITIQUE_ERRORS.labels(
                    step_name,
                    type(e).__name__,
                    "unexpected",
                ).inc()
                logger.error(
                    f"{step_name} failed: {e}",
                    exc_info=True,
                )
                if attempt == attempts:
                    return {
                        "error": str(e),
                        "provenance_id": provenance_id,
                    }
                await asyncio.sleep(1 * (2 ** (attempt - 1)))
            finally:
                latency = time.monotonic() - start_time
                CRITIQUE_LATENCY.labels(step_name).observe(latency)
                CRITIQUE_STEPS.labels(step_name).inc()


def chaos_injection(step_name: str, config: CritiqueConfig) -> None:
    if config.enable_chaos_injection and os.getenv("ENABLE_CHAOS_TESTING") == "true":
        r = random.random()
        if r < 0.1:
            logger.warning(f"CHAOS INJECTION: Simulating failure for {step_name}!")
            raise RuntimeError(f"Simulated chaos failure during {step_name}.")
        if r < 0.3:
            delay = random.uniform(0.5, 2.0)
            logger.warning(
                f"CHAOS INJECTION: Adding {delay:.2f}s latency to {step_name}."
            )
            time.sleep(delay)
    elif config.enable_chaos_injection:
        logger.warning(
            "Chaos injection enabled but ENABLE_CHAOS_TESTING "
            "not set to 'true'. Skipping injections."
        )


@plugin(
    kind=PlugInKind.CHECK,
    name="critique_agent",
    version="1.0.0",
    params_schema={
        "code_files": {
            "type": "dict",
            "description": "A dictionary mapping filenames to code content.",
        },
        "test_files": {
            "type": "dict",
            "description": "A dictionary mapping test filenames to their content.",
        },
        "requirements": {
            "type": "dict",
            "description": "The original requirements for the code.",
        },
        "state_summary": {
            "type": "string",
            "description": "A summary of the current system state.",
        },
        "config": {
            "type": "object",
            "description": "CritiqueConfig instance (Pydantic model).",
        },
    },
    description=(
        "Orchestrates a comprehensive critique pipeline including linting, "
        "testing, and security scanning to validate code."
    ),
    safe=True,
)
async def orchestrate_critique_pipeline(
    code_files: Dict[str, str],
    test_files: Dict[str, str],
    requirements: Dict[str, Any],
    state_summary: str,
    config: CritiqueConfig,
) -> Dict[str, Any]:
    """
    Main pipeline entrypoint.

    Exposed as an omnicore plugin. Tests patch resilient_step to assert
    correct wiring; real environments use the full resilient behavior.
    """
    with tracer.start_as_current_span(
        "orchestrate_critique_pipeline",
        attributes={"language": config.target_language},
    ):
        # --- Input validation ---
        if (
            not isinstance(code_files, dict)
            or not code_files
            or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in code_files.items()
            )
        ):
            CRITIQUE_ERRORS.labels(
                "orchestrate",
                "InvalidInput",
                "none",
            ).inc()
            raise ValueError(
                "code_files must be a non-empty dictionary with "
                "string keys and values."
            )

        if test_files:
            if not isinstance(test_files, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in test_files.items()
            ):
                CRITIQUE_ERRORS.labels(
                    "orchestrate",
                    "InvalidInput",
                    "none",
                ).inc()
                raise ValueError(
                    "test_files must be a dictionary with string keys and values."
                )

        # --- Config validation ---
        # Removed redundant CritiqueConfig instantiation.
        # The 'config' argument is now expected to be a valid CritiqueConfig instance.

        # Auto-detect language if needed
        if config.target_language == "auto":
            config.target_language = detect_language(code_files)
            logger.info(f"Auto-detected target language: {config.target_language}")

        results: Dict[str, Any] = {"provenance_chain": []}

        # --- Execution sandbox ---
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            await save_files_to_output(code_files, temp_dir)

            # Instantiate plugin once
            try:
                plugin_instance = get_plugin(
                    config.target_language,
                    config,
                )
            except ValueError as e:
                CRITIQUE_ERRORS.labels(
                    "orchestrate",
                    "PluginNotFound",
                    config.target_language,
                ).inc()
                return {
                    "error": f"Plugin instantiation failed: {str(e)}",
                }

            tasks: List[asyncio.Task] = []
            step_order: List[str] = []

            # --- Prepare parallelizable steps ---
            for step in config.pipeline_steps:
                provenance_id = generate_provenance_id()
                results["provenance_chain"].append({"step": step, "id": provenance_id})

                if step == "lint":
                    tasks.append(
                        asyncio.create_task(
                            resilient_step(
                                plugin_instance.lint,
                                step_name="lint",
                                config=config,
                                code_files=code_files,
                                temp_dir=temp_dir,
                            )
                        )
                    )
                    step_order.append("lint")

                elif step == "test":
                    tasks.append(
                        asyncio.create_task(
                            resilient_step(
                                plugin_instance.run_unit_tests,
                                step_name="unit_test",
                                config=config,
                                test_files=test_files,
                                code_files=code_files,
                                temp_dir=temp_dir,
                            )
                        )
                    )
                    step_order.append("test")

                elif step == "e2e_test" and config.enable_e2e_tests:
                    tasks.append(
                        asyncio.create_task(
                            resilient_step(
                                plugin_instance.run_e2e_tests,
                                step_name="e2e_test",
                                config=config,
                                code_files=code_files,
                                temp_dir=temp_dir,
                            )
                        )
                    )
                    step_order.append("e2e_test")

                elif step == "stress_test" and config.enable_stress_tests:
                    tasks.append(
                        asyncio.create_task(
                            resilient_step(
                                plugin_instance.run_stress_tests,
                                step_name="stress_test",
                                config=config,
                                code_files=code_files,
                                temp_dir=temp_dir,
                            )
                        )
                    )
                    step_order.append("stress_test")

                elif step == "security_scan" and config.enable_vulnerability_scan:
                    tools = config.vulnerability_scan_tools.get(
                        config.target_language,
                        [],
                    )
                    tasks.append(
                        asyncio.create_task(
                            resilient_step(
                                plugin_instance.vulnerability_scan,
                                step_name="security_scan",
                                config=config,
                                code_files=code_files,
                                temp_dir=temp_dir,
                                tools=tools,
                                timeout=config.tool_timeout_seconds,
                            )
                        )
                    )
                    step_order.append("security_scan")

                elif step in ("semantic", "fix"):
                    # These are handled sequentially after parallel phase.
                    logger.debug(f"Step {step} deferred for sequential execution.")
                else:
                    # Unknown steps should already be stripped by CritiqueConfig;
                    # if any slip through, we refuse to silently accept them.
                    logger.warning(f"Unknown step: {step}. Skipping.")

            # --- Run parallel tasks ---
            if tasks:
                parallel_results = await asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )
            else:
                parallel_results = []

            # Map results back
            for step_name, result in zip(step_order, parallel_results):
                if isinstance(result, Exception):
                    results[f"{step_name}_error"] = str(result)
                    continue

                if step_name == "lint":
                    results["lint_errors"] = result.get("all_errors", [])
                elif step_name == "test":
                    results["unit_test_pass_rate"] = result.get(
                        "pass_rate",
                        0.0,
                    )
                    results["coverage_percentage"] = result.get(
                        "coverage_percentage",
                        0.0,
                    )
                    CRITIQUE_COVERAGE.set(results["coverage_percentage"])
                elif step_name == "e2e_test":
                    results["e2e_test_results"] = result
                elif step_name == "stress_test":
                    results["stress_test_results"] = result
                elif step_name == "security_scan":
                    vulns = result.get("vulnerabilities", [])
                    results["vulnerabilities"] = vulns
                    for vuln in vulns:
                        CRITIQUE_VULNERABILITIES_FOUND.labels(
                            vuln.get("tool", "unknown"),
                            vuln.get("severity", "unknown"),
                        ).inc()

            # --- Sequential: Semantic critique ---
            if "semantic" in config.pipeline_steps:
                provenance_id = generate_provenance_id()
                results["provenance_chain"].append(
                    {"step": "semantic", "id": provenance_id}
                )

                try:
                    prompt = await build_semantic_critique_prompt(
                        code_files,
                        test_files,
                        requirements,
                        state_summary,
                        config={"language": config.target_language},
                    )
                    semantic_result = await resilient_step(
                        call_llm_for_critique,
                        prompt,
                        step_name="semantic",
                        config=config,
                    )
                except Exception as e:
                    semantic_result = {"error": str(e)}

                if isinstance(semantic_result, dict) and "error" in semantic_result:
                    results["semantic_error"] = semantic_result["error"]
                else:
                    # --- FIX: Ensure 'verdict' and 'score' are included from semantic_result ---
                    template = {
                        "semantic_alignment_score": 0.0,
                        "drift": [],
                        "hallucinations": [],
                        "test_quality_score": 0.0,
                        "test_suggestions": [],
                        "fixes_suggested": {},
                        "ambiguities": [],
                        "semantic_rationale": None,
                        "verdict": None,  # <-- ADDED to capture from mock
                        "score": 0.0,  # <-- ADDED to capture from mock
                    }
                    # --- END FIX ---

                    merged = {k: semantic_result.get(k, v) for k, v in template.items()}
                    results.update(merged)

                    if config.explainability:
                        rationale_prompt = (
                            "Explain the rationale behind this critique: "
                            f"{json.dumps(semantic_result, indent=2, default=str)}"
                        )
                        rationale = await resilient_step(
                            call_llm_for_critique,
                            rationale_prompt,
                            step_name="explain_rationale",
                            config=config,
                        )
                        if isinstance(rationale, dict):
                            results["semantic_rationale"] = rationale.get(
                                "content",
                                rationale.get(
                                    "error",
                                    "Failed to generate rationale.",
                                ),
                            )

            # --- Sequential: Auto-fix ---
            if "fix" in config.pipeline_steps:
                provenance_id = generate_provenance_id()
                results["provenance_chain"].append({"step": "fix", "id": provenance_id})

                fixes_suggested = results.get("fixes_suggested", {})
                if not isinstance(fixes_suggested, dict) or not fixes_suggested:
                    logger.info("No fixes suggested, skipping fix step.")
                    results["fixes_applied"] = False
                elif not all(
                    isinstance(k, str) and isinstance(v, list)
                    for k, v in fixes_suggested.items()
                ):
                    logger.warning(
                        "Invalid format for fixes_suggested, skipping fix step."
                    )
                    results["fix_error"] = "Invalid fixes_suggested format"
                    results["fixes_applied"] = False
                else:
                    try:
                        fix_result = await resilient_step(
                            apply_auto_fixes,
                            code_files,
                            fixes_suggested,
                            lang=config.target_language,
                            test_files=test_files,
                            hitl_enabled=config.hitl_callback is not None,
                            vc_path=os.getenv("GIT_REPO_PATH"),
                            step_name="fix",
                            config=config,
                        )
                        if isinstance(fix_result, dict) and "error" in fix_result:
                            results["fix_error"] = fix_result["error"]
                            results["fixes_applied"] = False
                        else:
                            # apply_auto_fixes returns modified code_files mapping
                            code_files = fix_result
                            await save_files_to_output(code_files, temp_dir)
                            results["fixes_applied"] = True
                    except Exception as e:
                        results["fix_error"] = str(e)
                        results["fixes_applied"] = False

        # --- FIX: Ensure 'code_files' key exists to satisfy test assertion ---
        results["code_files"] = code_files
        # --- END FIX ---

        await log_action(
            "Critique Pipeline Completed",
            {
                "final_results_summary": {
                    k: v for k, v in results.items() if k != "provenance_chain"
                }
            },
        )
        return results


class CritiqueAgent:
    """
    The orchestrator for automated code critique and quality analysis.
    Wraps the orchestrate_critique_pipeline function with a class-based interface
    for consistency with other generator agents.
    """

    def __init__(
        self,
        repo_path: Optional[str] = None,
        config: Optional[CritiqueConfig] = None,
        **kwargs,
    ):
        """
        Initialize the CritiqueAgent.

        Args:
            repo_path: Optional path to the repository being critiqued.
            config: Optional CritiqueConfig for pipeline configuration.
            **kwargs: Additional configuration options.
        """
        self.repo_path = repo_path
        self.config = config or CritiqueConfig()
        self.kwargs = kwargs
        logging.getLogger(__name__).info(
            f"CritiqueAgent initialized for repo: {repo_path}"
        )

    async def run(
        self,
        code_files: Dict[str, str],
        test_files: Optional[Dict[str, str]] = None,
        requirements: Optional[Dict[str, Any]] = None,
        state_summary: str = "",
        config: Optional[CritiqueConfig] = None,
    ) -> Dict[str, Any]:
        """
        Run the critique pipeline on the provided code.

        Args:
            code_files: Dictionary mapping filenames to code content.
            test_files: Optional dictionary mapping test filenames to content.
            requirements: Optional requirements specification.
            state_summary: State summary or context for the critique.
            config: Optional config override for this run.

        Returns:
            Dictionary containing critique results.
        """
        return await orchestrate_critique_pipeline(
            code_files=code_files,
            test_files=test_files or {},
            requirements=requirements or {},
            state_summary=state_summary,
            config=config or self.config,
        )

    async def critique(
        self,
        code_files: Dict[str, str],
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Convenience method to run critique with minimal parameters.

        Args:
            code_files: Dictionary mapping filenames to code content.
            **kwargs: Additional arguments passed to run().

        Returns:
            Dictionary containing critique results.
        """
        return await self.run(code_files=code_files, **kwargs)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run code critique pipeline",
    )
    parser.add_argument(
        "--code-dir",
        required=True,
        help="Directory containing code files",
    )
    parser.add_argument(
        "--test-dir",
        default="",
        help="Directory containing test files",
    )
    parser.add_argument(
        "--requirements-file",
        default="",
        help="File containing JSON requirements",
    )
    parser.add_argument(
        "--config",
        default="{}",
        help="JSON configuration for pipeline",
    )

    args = parser.parse_args()

    requirements: Dict[str, Any] = {}
    if args.requirements_file and Path(args.requirements_file).exists():
        try:
            with open(args.requirements_file, "r", encoding="utf-8") as f:
                requirements = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error loading requirements file: {e}")
            requirements = {}

    code_files: Dict[str, str] = {}
    for f in os.listdir(args.code_dir):
        p = Path(args.code_dir) / f
        if p.is_file():
            code_files[f] = p.read_text(encoding="utf-8")

    test_files: Dict[str, str] = {}
    if args.test_dir and Path(args.test_dir).is_dir():
        for f in os.listdir(args.test_dir):
            p = Path(args.test_dir) / f
            if p.is_file():
                test_files[f] = p.read_text(encoding="utf-8")

    try:
        config_data = json.loads(args.config)
        pipeline_config = CritiqueConfig(**config_data)
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"Error parsing config JSON or validating config: {e}")
        pipeline_config = CritiqueConfig()

    asyncio.run(
        orchestrate_critique_pipeline(
            code_files,
            test_files,
            requirements,
            "Auto-run from __main__",
            pipeline_config,
        )
    )
