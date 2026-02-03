"""
deploy_validator.py
Validates deployment configs for build success, security, and compliance.

Features:
- Async sandboxed validation (Docker build, Helm lint, Trivy/Snyk scan)
- Plugin registry for validators (config, security, compliance) with hot-reload.
- Structured report with build, lint, security, and compliance status.
- Auto-correction via LLM or templated fixes.
- Provenance and rationale logging.
- Security scanning and compliance tagging using Presidio and external tools.
- API and CLI for validator, with batch and streaming support.
- Observability: metrics, tracing, logging.
- Strict failure enforcement: no fallbacks for Presidio, missing handlers, or failed prompt optimization/summarization.
"""

import asyncio
import glob
import importlib.util  # Added for ValidatorRegistry plugin loading
import json
import os
import re  # Added for pattern matching
import shutil  # For tool availability checks
import sys  # Added for ValidatorRegistry
import tempfile  # For temporary files and directories
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Type

import aiofiles  # For asynchronous file operations

# Conditional aiohttp import for test environment compatibility
try:
    from aiohttp import web
    from aiohttp.web import Request, Response, RouteTableDef
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False
    # Provide type stubs for testing
    web = None
    Request = None
    Response = None
    RouteTableDef = None

from opentelemetry.trace import Status, StatusCode
from prometheus_client import Counter, Gauge, Histogram
from ruamel.yaml import (
    YAML as RuYAML,
)  # For advanced YAML operations (preserving comments)
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# --- FIX: Import scan_config_for_findings from the correct module ---
# REMOVED: from .deploy_response_handler import scan_config_for_findings
# --------------------------------------------------------------------

# --- CENTRAL RUNNER FOUNDATION ---
# Safe tracer import: works even if runner.tracer is not available
try:
    from runner import tracer as _runner_tracer  # type: ignore[attr-defined]

    tracer = _runner_tracer
except (ImportError, AttributeError):
    try:
        # fallback to opentelemetry if available
        from opentelemetry import trace as _otel_trace

        tracer = _otel_trace.get_tracer(__name__)
    except Exception:
        from contextlib import nullcontext

        class _NoopTracer:
            def start_as_current_span(self, *a, **k):
                return nullcontext()

        tracer = _NoopTracer()
# --- Presidio Imports (Strictly Required) ---
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from runner.llm_client import (
    call_ensemble_api,
)  # Central LLM Client for auto-correction
from runner.runner_errors import LLMError
# FIX: Import add_provenance from runner_audit to avoid circular dependency
from runner.runner_audit import log_audit_event as add_provenance
from runner.runner_logging import logger  # Use central logging and provenance
from runner.runner_metrics import LLM_ERRORS_TOTAL, LLM_LATENCY_SECONDS
from runner.runner_metrics import (
    LLM_REQUESTS_TOTAL as LLM_CALLS_TOTAL,
)  # Use central metrics

# -----------------------------------

# --- External Dependencies (Assumed to be real and production-ready) ---
# NOTE: Removed dependency on utils.summarize_text
# NOTE: Removed dependency on retry/stop_after_attempt/wait_exponential which are not built-in
# from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential # Assuming these were present for @retry


# NOTE: Using central logger imported above, local logger definition deleted.

# --- Prometheus Metrics ---
# NOTE: Local metrics retained for validator-specific statistics (non-LLM)
# FIX: Wrap metric creation in try-except to handle duplicate registration during pytest
try:
    validator_calls = Counter(
        "deploy_validator_calls_total",
        "Total validator calls by operation",
        ["target", "operation"],
    )
    validator_errors = Counter(
        "deploy_validator_errors_total",
        "Total validator errors by operation and type",
        ["target", "operation", "error_type"],
    )
    validator_latency = Histogram(
        "deploy_validator_latency_seconds",
        "Validator latency by operation",
        ["target", "operation"],
    )
    issue_count_gauge = Gauge(
        "deploy_validator_issue_count",
        "Number of issues found in the last validation",
        ["target", "issue_type_category"],
    )
    issue_total_found = Counter(
        "deploy_validator_issues_total",
        "Total cumulative issues found",
        ["target", "issue_type_category"],
    )
    # FIX: Add scan_total_findings metric that's used in scan_config_for_findings function
    scan_total_findings = Counter(
        "deploy_scan_total_findings",
        "Total security findings detected",
        ["format", "finding_type"],
    )
except ValueError:
    # Metrics already registered (happens during pytest collection)
    from prometheus_client import REGISTRY

    validator_calls = REGISTRY._names_to_collectors.get("deploy_validator_calls_total")
    validator_errors = REGISTRY._names_to_collectors.get(
        "deploy_validator_errors_total"
    )
    validator_latency = REGISTRY._names_to_collectors.get(
        "deploy_validator_latency_seconds"
    )
    issue_count_gauge = REGISTRY._names_to_collectors.get(
        "deploy_validator_issue_count"
    )
    issue_total_found = REGISTRY._names_to_collectors.get(
        "deploy_validator_issues_total"
    )
    scan_total_findings = REGISTRY._names_to_collectors.get(
        "deploy_scan_total_findings"
    )

# --- Security: PII/Secret & Dangerous Config Scanning Patterns ---
# NOTE: This DANGEROUS_CONFIG_PATTERNS is now used by the *imported* scan_config_for_findings
DANGEROUS_CONFIG_PATTERNS = {
    "PrivilegedContainer": r"(?i)privileged:\s*true",
    "HostPathMount": r"(?i)hostpath:\s*.*",  # Generic hostPath mount
    "RootUserInDockerfile": r"(?i)^user\s+root",  # Dockerfile USER root directive
    "ExposeAllPorts": r"(?i)expose\s+\d{1,5}\s*-\s*\d{1,5}",  # EXPOSE 80-9000
    "NoResourceLimits": r"(?i)resources:\s*\{\s*\}",  # Empty resources block in K8s (indicates missing limits/requests)
    "HardcodedCredentials_Pattern": r"(?i)password:\s*\S+|secret:\s*\S+|api_key:\s*\S+",  # Generic pattern for illustrative purposes
}


def scrub_text(text: str) -> str:
    """
    Strictly redacts sensitive information from the text using Presidio.
    Raises RuntimeError if Presidio fails during scrubbing.
    """
    if not text:
        return ""

    try:
        # FIX: Specify supported_languages to avoid warnings about non-English recognizers
        analyzer = AnalyzerEngine(supported_languages=["en"])
        anonymizer = AnonymizerEngine()

        # Define entities for Presidio to analyze (comprehensive standard list)
        presidio_entities = [
            "PERSON",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "CREDIT_CARD",
            "US_SSN",
            "IP_ADDRESS",
            "URL",
            "NRP",
        ]  # NRP: National ID

        # Analyze the text for sensitive information
        results = analyzer.analyze(text=text, entities=presidio_entities, language="en")

        # Anonymize identified entities with a generic '[REDACTED]' replacement
        anonymized_text = anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators={"DEFAULT": OperatorConfig("replace", {"new_value": "[REDACTED]"})},
        ).text

        return anonymized_text

    except Exception as e:
        logger.error(
            "Presidio PII/secret scrubbing failed critically: %s", e, exc_info=True
        )
        # In a strict-fail model, re-raise the exception if scrubbing cannot be performed
        raise RuntimeError(
            f"Critical error during sensitive data scrubbing with Presidio: {e}"
        ) from e


# --- FIX: MOVED scan_config_for_findings function here ---
async def scan_config_for_findings(
    config_text: str, config_format: str, dangerous_patterns: Dict[str, str]
) -> List[Dict[str, str]]:
    """
    Scans the configuration text for secrets and dangerous configurations.
    Uses centralized `scrub_text` on inputs (applied upstream) and directly
    uses external tools like Trivy for misconfigurations.
    Returns a list of dictionaries, each describing a finding.

    FIX: Added `dangerous_patterns` argument so it can be passed from the caller module.
    """
    findings: List[Dict[str, str]] = []

    # --- Dangerous/Misconfiguration Pattern Matching ---
    for finding_name, pattern_regex in dangerous_patterns.items():
        # FIX: Use re.MULTILINE flag so ^ and $ match start/end of each line, not just start/end of string
        if re.search(pattern_regex, config_text, re.MULTILINE):
            findings.append(
                {
                    "type": "Misconfiguration_Pattern",
                    "category": finding_name,
                    "description": f"Detected: {finding_name}",
                    "severity": "High",
                }
            )
            scan_total_findings.labels(
                format=config_format, finding_type=f"Misconfig_{finding_name}"
            ).inc()

    # --- External Tool Scan with Trivy (for Infrastructure as Code misconfigurations, CVEs, etc.) ---
    # Trivy often needs the config as a file. Use a temp file for this.
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_config_path = (
            Path(temp_dir)
            / f"config.{config_format.lower().replace('dockerfile', 'docker')}"
        )  # Use common file extension
        try:
            # Write the config text to a temporary file. It's assumed `config_text` is already scrubbed.
            # Using aiofiles.open for async file write
            async with aiofiles.open(temp_config_path, mode="w", encoding="utf-8") as f:
                await f.write(config_text)

            # FIX: Check if trivy is available before attempting to use it
            if not shutil.which("trivy"):
                findings.append(
                    {
                        "type": "ToolWarning",
                        "category": "TrivyNotInstalled",
                        "description": "Trivy command not found. Security scanning skipped gracefully. Install Trivy for enhanced security validation.",
                        "severity": "Low",
                    }
                )
                logger.warning(
                    "Trivy command not found. Skipping Trivy scan. Install Trivy (https://trivy.dev) for enhanced security scanning."
                )
                scan_total_findings.labels(
                    format=config_format, finding_type="Trivy_NotFound"
                ).inc()
            else:
                trivy_command = [
                    "trivy",
                    "config",
                    "--format",
                    "json",  # Request JSON output for easy parsing
                    "--severity",
                    "CRITICAL,HIGH",  # Focus on high severity issues
                    "--quiet",  # Suppress verbose output
                    str(temp_config_path),
                ]

                process = await asyncio.create_subprocess_exec(
                    *trivy_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=1024
                    * 1024,  # Set a buffer limit to prevent very large outputs from hanging
                )
                stdout, stderr = await process.communicate()

                if process.returncode in [0, 1]:  # 0 means no issues, 1 means issues found
                    trivy_output_str = stdout.decode("utf-8").strip()
                    if trivy_output_str:
                        try:
                            trivy_results = json.loads(trivy_output_str)
                            for result_section in trivy_results.get("Results", []):
                                # Trivy can find 'Misconfigurations', 'Vulnerabilities', etc.
                                for misconfig in result_section.get(
                                    "Misconfigurations", []
                                ):
                                    findings.append(
                                        {
                                            "type": "Misconfiguration_Trivy",
                                            "category": misconfig.get("Type", "N/A"),
                                            "description": misconfig.get(
                                                "Title", "No Title"
                                            )
                                            + ": "
                                            + misconfig.get("Description", ""),
                                            "severity": misconfig.get(
                                                "Severity", "Unknown"
                                            ),
                                        }
                                    )
                                    scan_total_findings.labels(
                                        format=config_format, finding_type="Trivy_Misconfig"
                                    ).inc()
                                for vuln in result_section.get("Vulnerabilities", []):
                                    findings.append(
                                        {
                                            "type": "Vulnerability_Trivy",
                                            "category": vuln.get("VulnerabilityID", "N/A"),
                                            "description": vuln.get("Title", "No Title"),
                                            "severity": vuln.get("Severity", "Unknown"),
                                        }
                                    )
                                    scan_total_findings.labels(
                                        format=config_format,
                                        finding_type="Trivy_Vulnerability",
                                    ).inc()
                        except json.JSONDecodeError:
                            findings.append(
                                {
                                    "type": "ToolError_Trivy",
                                    "category": "OutputParse",
                                    "description": "Trivy produced invalid JSON output.",
                                    "severity": "Medium",
                                }
                            )
                            scan_total_findings.labels(
                                format=config_format, finding_type="Trivy_ParseError"
                            ).inc()
                    if stderr:
                        logger.warning(
                            f"Trivy stderr for scan_config: {stderr.decode('utf-8').strip()}"
                        )
                else:
                    findings.append(
                        {
                            "type": "ToolError_Trivy",
                            "category": "Execution",
                            "description": f"Trivy command failed with exit code {process.returncode}: {stderr.decode('utf-8').strip()}",
                            "severity": "High",
                        }
                    )
                    scan_total_findings.labels(
                        format=config_format, finding_type="Trivy_ExecError"
                    ).inc()
        except FileNotFoundError:
            findings.append(
                {
                    "type": "ToolWarning",
                    "category": "TrivyNotInstalled",
                    "description": "Trivy command not found. Security scanning skipped gracefully. Install Trivy for enhanced security validation.",
                    "severity": "Low",
                }
            )
            logger.warning(
                "Trivy command not found. Skipping Trivy scan. Install Trivy (https://trivy.dev) for enhanced security scanning."
            )  # Warning level for missing optional tool
            scan_total_findings.labels(
                format=config_format, finding_type="Trivy_NotFound"
            ).inc()
        except Exception as e:
            findings.append(
                {
                    "type": "ToolError_Trivy",
                    "category": "Unexpected",
                    "description": f"Unexpected error running Trivy: {e}",
                    "severity": "High",
                }
            )
            logger.error(f"Unexpected error running Trivy: {e}", exc_info=True)
            scan_total_findings.labels(
                format=config_format, finding_type="Trivy_UnexpectedError"
            ).inc()

    # Update gauge with current number of unique findings.
    # FIX: Use 'target' instead of 'format' to match the metric label definition
    issue_count_gauge.labels(
        target=config_format, issue_type_category="OverallFindingsCount"
    ).set(len(findings))

    return findings


# --- END OF MOVED FUNCTION ---


class Validator(ABC):
    """Abstract base class for validators for different configuration formats."""

    __version__ = "1.0"
    __source__ = (
        "default"  # Indicates if it's a built-in or dynamically loaded validator
    )

    @abstractmethod
    async def validate(self, config_content: str, target_type: str) -> Dict[str, Any]:
        """
        Validates the configuration content for a specific target type.
        Returns a detailed report including build status, lint issues, security findings, etc.
        Must raise exceptions on critical validation failures.
        """
        pass

    @abstractmethod
    async def fix(
        self, config_content: str, issues: List[str], target_type: str
    ) -> str:
        """
        Attempts to fix detected issues in the configuration content using an LLM.
        Returns the fixed configuration string. Must raise exceptions on fix failure.
        """
        pass


class DockerValidator(Validator):
    __version__ = "1.2"  # Example: bumped version for more robust checks
    __source__ = "built-in"

    async def validate(self, config_content: str, target_type: str) -> Dict[str, Any]:
        """Validates a Dockerfile for build success, linting, and basic security checks."""
        report = {
            "build_status": "unknown",
            "build_output": "",
            "lint_status": "unknown",  # FIX: Add lint_status field expected by tests
            "lint_issues": [],
            "security_findings": [],
            "compliance_score": 0.0,  # Will be calculated
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            dockerfile_path = temp_dir_path / "Dockerfile"

            try:
                # Corrected to use aiofiles.open for async file write
                async with aiofiles.open(
                    dockerfile_path, mode="w", encoding="utf-8"
                ) as f:
                    await f.write(config_content)

                # FIX: Check if Docker is available before attempting to use it
                if not shutil.which("docker"):
                    logger.warning("Docker tool not found. Skipping Docker build test.")
                    report["build_status"] = "skipped"
                    report["lint_issues"].append(
                        "Docker tool not available. Install docker to enable build validation."
                    )
                else:
                    # 1. Docker Build Test
                    build_proc = await asyncio.create_subprocess_exec(
                        "docker",
                        "build",
                        "-f",
                        str(dockerfile_path),
                        "--no-cache",
                        ".",
                        cwd=temp_dir_path,  # Set cwd to temp_dir_path for build context
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await build_proc.communicate()

                    report["build_output"] = stdout.decode("utf-8") + stderr.decode("utf-8")
                    if build_proc.returncode == 0:
                        report["build_status"] = "success"
                    else:
                        report["build_status"] = "failed"
                        report["lint_issues"].append(
                            f"Dockerfile failed to build: {stderr.decode('utf-8').strip()}"
                        )
                        issue_total_found.labels(
                            target=target_type, issue_type_category="BuildError"
                        ).inc()

                # 2. Lint with Hadolint
                # FIX: Check if hadolint is available before attempting to use it
                if not shutil.which("hadolint"):
                    logger.warning("Hadolint tool not found. Skipping Dockerfile linting.")
                    report["lint_issues"].append(
                        "Hadolint tool not available. Install hadolint to enable Dockerfile linting."
                    )
                else:
                    try:
                        hadolint_proc = await asyncio.create_subprocess_exec(
                            "hadolint",
                            str(dockerfile_path),
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        lint_stdout, lint_stderr = await hadolint_proc.communicate()
                        # FIX: Only add lint issues when hadolint actually reports problems (returncode != 0)
                        # When returncode is 0, hadolint found no issues, so don't add output to lint_issues
                        if hadolint_proc.returncode != 0:
                            lint_output_lines = (
                                lint_stdout.decode().splitlines()
                                + lint_stderr.decode().splitlines()
                            )
                            report["lint_issues"].extend(
                                [line for line in lint_output_lines if line.strip()]
                            )
                            issue_total_found.labels(
                                target=target_type, issue_type_category="HadolintLint"
                            ).inc(len(lint_output_lines))
                        # FIX: Set lint_status based on hadolint results
                        if hadolint_proc.returncode == 0:
                            report["lint_status"] = "success"
                        else:
                            report["lint_status"] = (
                                "warning"
                                if report["build_status"] == "success"
                                else "failed"
                            )
                        if (
                            hadolint_proc.returncode != 0
                            and report["build_status"] == "success"
                        ):
                            report["build_status"] = "lint_warning"

                    except FileNotFoundError:
                        report["lint_issues"].append(
                            "Hadolint not found. Skipping linting."
                        )
                        report["lint_status"] = (
                            "skipped"  # FIX: Set status when tool not found
                        )
                        logger.warning(
                            "Hadolint command not found. Please install hadolint for comprehensive Dockerfile linting."
                        )
                        issue_total_found.labels(
                            target=target_type, issue_type_category="HadolintNotFound"
                        ).inc()
                    except Exception as e:
                        report["lint_issues"].append(
                            f"Error during Hadolint execution: {e}"
                        )
                        report["lint_status"] = "error"  # FIX: Set status on error
                        logger.error(
                            "Error during Hadolint execution: %s", e, exc_info=True
                        )
                        issue_total_found.labels(
                            target=target_type, issue_type_category="HadolintError"
                        ).inc()

                # 3. Security Findings
                # --- FIX: Pass DANGEROUS_CONFIG_PATTERNS to the imported function ---
                report["security_findings"] = await scan_config_for_findings(
                    config_content, target_type, DANGEROUS_CONFIG_PATTERNS
                )

                # Calculate compliance score
                # FIX: Use more strict scoring (divide by 5 instead of 10) so issues have bigger impact
                total_issues = len(report["lint_issues"]) + len(
                    report["security_findings"]
                )
                report["compliance_score"] = (
                    1.0 if total_issues == 0 else max(0.0, 1.0 - (total_issues / 5.0))
                )

            except FileNotFoundError as e:
                report["build_status"] = "tool_not_found"
                report["build_output"] = (
                    f"Required tool not found: {e}"  # Fixed to show error
                )
                logger.error(
                    "Required tool not found for Docker validation: %s",
                    e,
                    exc_info=True,
                )
                report["lint_issues"].append(
                    f"Required Docker build tool not found: {e}"
                )
                issue_total_found.labels(
                    target=target_type, issue_type_category="ToolNotFound"
                ).inc()
            except Exception as e:
                report["build_status"] = "internal_error"
                report["build_output"] = f"Internal validation error: {e}"
                logger.error(
                    "Internal error during Docker validation: %s", e, exc_info=True
                )
                report["lint_issues"].append(f"Internal validator error: {e}")
                issue_total_found.labels(
                    target=target_type, issue_type_category="InternalError"
                ).inc()

        return report

    async def fix(
        self, config_content: str, issues: List[str], target_type: str
    ) -> str:
        """Attempts to fix Dockerfile issues using an LLM."""
        fix_prompt = f"Fix these issues in the Dockerfile:\n{json.dumps(issues, indent=2)}\n\nOriginal Dockerfile:\n```dockerfile\n{config_content}\n```\n\nProvide ONLY the corrected Dockerfile content. Do not add any conversational text or markdown wrappers."

        try:
            start_time = time.time()
            # --- Use call_ensemble_api for LLM-based fixing ---
            fixed_response = await call_ensemble_api(
                fix_prompt,
                [{"model": "gpt-4o"}],
                voting_strategy="majority",
                stream=False,
            )

            LLM_CALLS_TOTAL.labels(
                provider="deploy_validator", model="gpt-4o"
            ).inc()  # Removed non-standard 'task' label
            LLM_LATENCY_SECONDS.labels(
                provider="deploy_validator", model="gpt-4o"
            ).observe(time.time() - start_time)
            await add_provenance("fix_docker_config", {"action": "fix_docker_config", "model": "gpt-4o"})

            # The LLM client returns a structured dict: {'content': '...', 'model': ...}
            fixed_config_content = fixed_response.get("content", "").strip()

            if not fixed_config_content:
                LLM_ERRORS_TOTAL.labels(
                    provider="deploy_validator",
                    model="gpt-4o",
                    error_type="EmptyLLMResponse",
                ).inc()
                raise ValueError("LLM returned empty content for Dockerfile fix.")

            # Clean up potential markdown fences
            fixed_config_content = re.sub(
                r"^```(dockerfile)?\n", "", fixed_config_content, flags=re.IGNORECASE
            )
            fixed_config_content = re.sub(r"\n```$", "", fixed_config_content)

            return fixed_config_content
        except Exception as e:
            if not isinstance(e, LLMError):
                LLM_ERRORS_TOTAL.labels(
                    provider="deploy_validator",
                    model="gpt-4o",
                    error_type=type(e).__name__,
                ).inc()
            logger.error(
                "Failed to fix Dockerfile issues using LLM: %s", e, exc_info=True
            )
            raise RuntimeError(f"Failed to auto-fix Dockerfile issues: {e}") from e


class HelmValidator(Validator):
    __version__ = "1.1"
    __source__ = "built-in"

    async def validate(self, config_content: str, target_type: str) -> Dict[str, Any]:
        """Validates a Helm chart by linting and running security scans."""
        report = {
            "lint_status": "unknown",
            "lint_output": "",
            "lint_issues": [],
            "security_findings": [],
            "compliance_score": 0.0,
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            chart_path = Path(tmp_dir) / "mychart"
            chart_path.mkdir()
            chart_yaml_path = chart_path / "Chart.yaml"

            try:
                chart_data = RuYAML().load(config_content)
                if (
                    isinstance(chart_data, dict)
                    and "apiVersion" in chart_data
                    and "name" in chart_data
                ):
                    # Corrected to use aiofiles.open for async file write
                    async with aiofiles.open(
                        chart_yaml_path, mode="w", encoding="utf-8"
                    ) as f:
                        await f.write(config_content)
                else:
                    # Fallback for when content is not a Chart.yaml (e.g., it's a values.yaml or template snippet)
                    async with aiofiles.open(
                        chart_yaml_path, mode="w", encoding="utf-8"
                    ) as f:
                        await f.write(
                            "apiVersion: v2\nname: temp-chart\nversion: 0.1.0\n"
                        )
                    async with aiofiles.open(
                        chart_path / "values.yaml", mode="w", encoding="utf-8"
                    ) as f:
                        await f.write(config_content)
                    templates_path = chart_path / "templates"
                    templates_path.mkdir()
                    async with aiofiles.open(
                        templates_path / "NOTES.txt", mode="w", encoding="utf-8"
                    ) as f:
                        await f.write("")

                # 1. Helm Lint
                helm_lint_proc = await asyncio.create_subprocess_exec(
                    "helm",
                    "lint",
                    str(chart_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                lint_stdout, lint_stderr = await helm_lint_proc.communicate()

                report["lint_output"] = lint_stdout.decode(
                    "utf-8"
                ) + lint_stderr.decode("utf-8")
                if helm_lint_proc.returncode == 0:
                    report["lint_status"] = "success"
                else:
                    report["lint_status"] = "failed"
                    # FIX: Make filtering case-insensitive to catch "Error:", "error:", "ERROR", etc.
                    report["lint_issues"].extend(
                        [
                            line
                            for line in report["lint_output"].splitlines()
                            if line.strip()
                            and ("error" in line.lower() or "warning" in line.lower())
                        ]
                    )
                    issue_total_found.labels(
                        target=target_type, issue_type_category="HelmLint"
                    ).inc(len(report["lint_issues"]))

                # 2. Security Scan
                # --- FIX: Pass DANGEROUS_CONFIG_PATTERNS to the imported function ---
                report["security_findings"] = await scan_config_for_findings(
                    config_content, target_type, DANGEROUS_CONFIG_PATTERNS
                )

                # Calculate compliance score
                # FIX: Use more strict scoring (divide by 5 instead of 10) so issues have bigger impact
                total_issues = len(report.get("lint_issues", [])) + len(
                    report.get("security_findings", [])
                )
                report["compliance_score"] = (
                    1.0 if total_issues == 0 else max(0.0, 1.0 - (total_issues / 5.0))
                )

            except FileNotFoundError as e:
                report["lint_status"] = "tool_not_found"
                report["lint_output"] = (
                    f"Required tool not found: {e}"  # Fixed to show error
                )
                logger.error("Required Helm tool not found: %s", e, exc_info=True)
                report["lint_issues"].append(f"Required Helm tool not found: {e}")
                issue_total_found.labels(
                    target=target_type, issue_type_category="ToolNotFound"
                ).inc()
            except Exception as e:
                report["lint_status"] = "internal_error"
                report["lint_output"] = f"Internal validation error: {e}"
                logger.error(
                    "Internal error during Helm validation: %s", e, exc_info=True
                )
                report["lint_issues"].append(f"Internal validator error: {e}")
                issue_total_found.labels(
                    target=target_type, issue_type_category="InternalError"
                ).inc()

        return report

    async def fix(
        self, config_content: str, issues: List[str], target_type: str
    ) -> str:
        """Attempts to fix Helm chart issues using an LLM."""
        fix_prompt = f"Fix these issues in the Helm chart YAML:\n{json.dumps(issues, indent=2)}\n\nOriginal Helm Chart YAML:\n```yaml\n{config_content}\n```\n\nProvide ONLY the corrected Helm chart YAML content. Do not add any conversational text or markdown wrappers."

        try:
            start_time = time.time()
            # --- Use call_ensemble_api for LLM-based fixing ---
            fixed_response = await call_ensemble_api(
                fix_prompt,
                [{"model": "gpt-4o"}],
                voting_strategy="majority",
                stream=False,
            )

            LLM_CALLS_TOTAL.labels(
                provider="deploy_validator", model="gpt-4o"
            ).inc()  # Removed non-standard 'task' label
            LLM_LATENCY_SECONDS.labels(
                provider="deploy_validator", model="gpt-4o"
            ).observe(time.time() - start_time)
            await add_provenance("fix_helm_config", {"action": "fix_helm_config", "model": "gpt-4o"})

            fixed_config_content = fixed_response.get("content", "").strip()

            if not fixed_config_content:
                LLM_ERRORS_TOTAL.labels(
                    provider="deploy_validator",
                    model="gpt-4o",
                    error_type="EmptyLLMResponse",
                ).inc()
                raise ValueError("LLM returned empty content for Helm fix.")

            # Clean up potential markdown fences
            fixed_config_content = re.sub(
                r"^```(yaml)?\n", "", fixed_config_content, flags=re.IGNORECASE
            )
            fixed_config_content = re.sub(r"\n```$", "", fixed_config_content)

            return fixed_config_content
        except Exception as e:
            if not isinstance(e, LLMError):
                LLM_ERRORS_TOTAL.labels(
                    provider="deploy_validator",
                    model="gpt-4o",
                    error_type=type(e).__name__,
                ).inc()
            logger.error(
                "Failed to fix Helm chart issues using LLM: %s", e, exc_info=True
            )
            raise RuntimeError(f"Failed to auto-fix Helm chart issues: {e}") from e


# NOTE: The dependency on HandlerRegistry (and its internal FormatHandler) means
# that `deploy_validator` should only import the Registry if it is guaranteed to
# be available. The original design forces this via the `repair_sections` function.
# --- FIX: `repair_sections` and `enrich_config_output` have been REMOVED ---
# They belong in `deploy_response_handler.py` to break the circular dependency.


class ValidatorRegistry:
    """
    Registry for validators with hot-reload capability.
    Discovers `Validator` implementations from a specified plugin directory
    and provides access to them by target type.
    """

    def __init__(self, plugin_dir: str = "validator_plugins"):
        self.plugin_dir = plugin_dir
        self.validators: Dict[str, Type[Validator]] = (
            {}
        )  # Stores validator classes (not instances)
        self.validator_info: Dict[str, Dict[str, Any]] = (
            {}
        )  # Stores metadata about validators
        self._load_plugins()  # Initial load of plugins
        self._setup_hot_reload()  # Setup watchdog for hot-reloading

    def _load_plugins(self):
        """
        Loads built-in validators and discovers custom Validator implementations
        from the plugin directory. Custom validators overwrite built-in ones if names conflict.
        """
        self.validators.clear()
        self.validator_info.clear()

        # 2. Load built-in validators first
        built_in_validators = {
            "docker": DockerValidator,
            "helm": HelmValidator,
        }
        for tgt, validator_class in built_in_validators.items():
            self.validators[tgt] = validator_class
            self.validator_info[tgt] = {
                "version": validator_class.__version__,
                "source": validator_class.__source__,
            }

        # 3. Add plugin directory to sys.path for module discovery
        abs_plugin_dir = str(Path(self.plugin_dir).resolve())
        if abs_plugin_dir not in sys.path:
            sys.path.insert(0, abs_plugin_dir)

        # 4. Discover and load validators from plugin files
        for file_path in glob.glob(f"{self.plugin_dir}/*_validator.py"):
            if file_path.endswith("__init__.py") or file_path.endswith("_test.py"):
                continue

            module_name_base = Path(file_path).stem
            unique_module_name = (
                f"dynamic_validator_{module_name_base}_{uuid.uuid4().hex}"
            )

            spec = importlib.util.spec_from_file_location(unique_module_name, file_path)
            if spec is None or spec.loader is None:
                logger.warning(
                    "Could not find module spec for plugin file: %s", file_path
                )
                continue

            try:
                # Use importlib.util.module_from_spec for dynamic loading
                module = importlib.util.module_from_spec(spec)
                sys.modules[unique_module_name] = module
                spec.loader.exec_module(module)

                found_custom_validator = False
                for name, obj in vars(module).items():
                    if (
                        isinstance(obj, type)
                        and issubclass(obj, Validator)
                        and obj != Validator
                    ):
                        tgt_key = name.lower().replace("validator", "")
                        self.validators[tgt_key] = obj
                        self.validator_info[tgt_key] = {
                            "version": getattr(obj, "__version__", "unknown"),
                            "source": file_path,
                        }
                        logger.info(
                            "Loaded custom validator: %s from %s (version: %s).",
                            tgt_key,
                            file_path,
                            getattr(obj, "__version__", "unknown"),
                        )
                        found_custom_validator = True
                if not found_custom_validator:
                    logger.warning(
                        "No valid Validator class found in plugin file: %s. Ensure it inherits from Validator.",
                        file_path,
                    )
            except Exception as e:
                logger.error(
                    "Failed to load custom validator from %s: %s",
                    file_path,
                    e,
                    exc_info=True,
                )
                if unique_module_name in sys.modules:
                    del sys.modules[unique_module_name]

        logger.info(
            "Validator registry loaded %d validators (including built-in and custom).",
            len(self.validators),
        )

    def reload_plugins(self):
        """
        Reloads all validators.
        """
        self._load_plugins()
        logger.info("Validators reloaded due to file system change.")

    def _setup_hot_reload(self):
        """Sets up a Watchdog observer to monitor the plugin directory for changes."""
        # --- FIX: Guard hot-reload for testing environments ---
        if os.getenv("TESTING") == "1":
            logger.info(
                "TESTING environment detected. Skipping hot-reload observer setup."
            )
            return
        # --- End Fix ---

        # Check if the directory exists before starting the observer
        if not Path(self.plugin_dir).exists():
            logger.warning(
                "Plugin directory '%s' does not exist. Skipping hot-reload setup.",
                self.plugin_dir,
            )
            return

        class ReloadHandler(FileSystemEventHandler):
            def __init__(self, registry_instance: "ValidatorRegistry"):
                self.registry_instance = registry_instance

            def dispatch(self, event):
                if (
                    not event.is_directory
                    and event.src_path.endswith(".py")
                    and event.event_type in ("created", "modified", "deleted")
                ):
                    logger.info(
                        "Validator plugin file changed: %s (Event: %s). Triggering reload.",
                        event.src_path,
                        event.event_type,
                    )
                    self.registry_instance.reload_plugins()

        observer = Observer()
        observer.schedule(ReloadHandler(self), self.plugin_dir, recursive=False)
        observer.start()
        logger.info(
            "Started hot-reload observer for validator plugins in: %s", self.plugin_dir
        )

    def get_validator(self, target: str) -> Validator:
        """
        Retrieves an instantiated validator for the specified target.
        """
        validator_class = self.validators.get(target.lower())
        if validator_class:
            return validator_class()

        raise ValueError(
            f"No validator found for target '{target}'. Please implement and register a validator for this target in '{self.plugin_dir}'."
        )


# --- FIX: Deleted repair_sections (lines 378-467) ---
# --- FIX: Deleted enrich_config_output (lines 469-502) ---


# --- API with aiohttp ---
# Conditionally create API routes only if aiohttp is available
if HAS_AIOHTTP:
    routes = RouteTableDef()
    api_semaphore = asyncio.Semaphore(5)


    @routes.post("/validate")
    async def api_validate(request: Request) -> Response:
        """
        API endpoint to validate a configuration file.
        """
        with tracer.start_as_current_span("api_validate") as span:
            time.time()
            target = "unknown"  # Set initial target for error logging
            try:
                data = await request.json()
                config_content = data.get("config_content")
                target = data.get("target", "docker")

                span.set_attribute("target", target)

                if not config_content:
                    raise web.HTTPBadRequest(reason="'config_content' is required.")

                # --- FIX: Use singleton registry from app context ---
                validator_registry: ValidatorRegistry = request.app["validator_registry"]
                validator = validator_registry.get_validator(target)
                # --------------------------------------------------

                result = await validator.validate(config_content, target)

                # Scrub the output report to ensure no secrets/PII are returned
                scrubbed_result = json.loads(scrub_text(json.dumps(result)))

                span.set_status(Status(StatusCode.OK))
                return web.json_response(scrubbed_result)
            except web.HTTPError as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            except ValueError as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return web.json_response(
                    {"status": "error", "message": f"Validation setup error: {str(e)}"},
                    status=400,
                )
            except Exception as e:
                logger.error(
                    "API /validate encountered an error for target %s: %s",
                    target,
                    e,
                    exc_info=True,
                )
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return web.json_response({"status": "error", "message": str(e)}, status=500)


    @routes.post("/fix")
    async def api_fix(request: Request) -> Response:
        """
        API endpoint to fix a configuration file using LLM auto-correction.
        """
        with tracer.start_as_current_span("api_fix") as span:
            time.time()
            target = "unknown"  # Set initial target for error logging
            try:
                data = await request.json()
                config_content = data.get("config_content")
                issues = data.get("issues", [])
                target = data.get("target", "docker")

                span.set_attribute("target", target)

                if not config_content or not issues:
                    raise web.HTTPBadRequest(
                        reason="'config_content' and 'issues' are required."
                    )

                # --- FIX: Use singleton registry from app context ---
                validator_registry: ValidatorRegistry = request.app["validator_registry"]
                validator = validator_registry.get_validator(target)
                # --------------------------------------------------

                fixed_content = await validator.fix(config_content, issues, target)

                span.set_status(Status(StatusCode.OK))
                return web.json_response(
                    {"status": "success", "fixed_content": fixed_content}
                )
            except web.HTTPError as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            except (ValueError, RuntimeError) as e:
                logger.error(
                    "API /fix failed to fix config for target %s: %s",
                    target,
                    e,
                    exc_info=True,
                )
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return web.json_response(
                    {"status": "error", "message": f"Auto-fix failed: {str(e)}"}, status=424
                )  # Failed Dependency
            except Exception as e:
                logger.error(
                    "API /fix encountered an error for target %s: %s",
                    target,
                    e,
                    exc_info=True,
                )
                span.set_status(Status(StatusCode.ERROR, str(e)))
                return web.json_response({"status": "error", "message": str(e)}, status=500)


    app = web.Application()
    app.add_routes(routes)


    # --- FIX: Add startup event to create singleton registry ---
    async def start_background_tasks(app: web.Application):
        """
        On server startup, create the singleton ValidatorRegistry.
        This starts the watchdog observer *once*.
        """
        logger.info("Server starting up... Initializing ValidatorRegistry singleton.")
        # Ensure plugin directory exists for registry startup
        Path("validator_plugins").mkdir(exist_ok=True)
        app["validator_registry"] = ValidatorRegistry()
        logger.info("ValidatorRegistry singleton initialized.")


    app.on_startup.append(start_background_tasks)
    # ------------------------------------------------------
else:
    # If aiohttp is not available, provide stub objects for import compatibility
    routes = None
    app = None
    api_semaphore = None
    
    async def api_validate(*args, **kwargs):
        raise ImportError("aiohttp is not installed. API endpoints are not available.")
    
    async def api_fix(*args, **kwargs):
        raise ImportError("aiohttp is not installed. API endpoints are not available.")
    
    async def start_background_tasks(*args, **kwargs):
        raise ImportError("aiohttp is not installed. API endpoints are not available.")
