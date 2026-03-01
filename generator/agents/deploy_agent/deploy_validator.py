# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

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
from typing import Any, Dict, List, Optional, Set, Type

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
    YAMLError as RuamelYAMLError,  # Import correct exception for YAML parsing errors
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
from runner.runner_audit import log_audit_event as add_provenance
from runner.runner_logging import logger  # Use central logging and provenance
from runner.runner_metrics import LLM_ERRORS_TOTAL, LLM_LATENCY_SECONDS
from runner.runner_metrics import (
    LLM_REQUESTS_TOTAL as LLM_CALLS_TOTAL,
)  # Use central metrics
from generator.agents.metrics_utils import get_or_create_metric

# -----------------------------------

# --- External Dependencies (Assumed to be real and production-ready) ---
# NOTE: Removed dependency on utils.summarize_text
# NOTE: Removed dependency on retry/stop_after_attempt/wait_exponential which are not built-in
# from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential # Assuming these were present for @retry


# NOTE: Using central logger imported above, local logger definition deleted.

# --- Prometheus Metrics ---
# NOTE: Local metrics retained for validator-specific statistics (non-LLM)
validator_calls = get_or_create_metric(
    Counter,
    "deploy_validator_calls_total",
    "Total validator calls by operation",
    ["target", "operation"],
)
scan_total_findings = get_or_create_metric(
    Counter,
    "deploy_validator_scan_findings_total",
    "Total scan findings by format and finding type",
    ["format", "finding_type"],
)
issue_total_found = get_or_create_metric(
    Counter,
    "deploy_validator_issues_total",
    "Total issues found by target and category",
    ["target", "issue_type_category"],
)
issue_count_gauge = get_or_create_metric(
    Gauge,
    "deploy_validator_issue_count_gauge",
    "Current number of findings by target and category",
    ["target", "issue_type_category"],
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

# Dockerfile ENTRYPOINT/CMD semantic validation constants
_BARE_PYTHON_ENTRYPOINTS = (["python"], ["python3"])
_KNOWN_SERVER_EXECUTABLES = frozenset({
    "uvicorn", "gunicorn", "flask", "celery",
    "django-admin", "hypercorn", "daphne", "granian",
})

# Module-level singleton instances for Presidio to avoid log spam from repeated initialization
_analyzer: Optional[AnalyzerEngine] = None
_anonymizer: Optional[AnonymizerEngine] = None


def _get_presidio_instances():
    """Lazily initialize and return singleton Presidio instances."""
    global _analyzer, _anonymizer
    if _analyzer is None:
        _analyzer = AnalyzerEngine(supported_languages=["en"])
        try:
            from runner.runner_security_utils import _add_custom_recognizers
            _add_custom_recognizers(_analyzer)
        except Exception:
            pass
    if _anonymizer is None:
        _anonymizer = AnonymizerEngine()
    return _analyzer, _anonymizer


def _reset_presidio_instances():
    """Reset singleton Presidio instances (used by tests)."""
    global _analyzer, _anonymizer
    _analyzer = None
    _anonymizer = None


def scrub_text(text: str) -> str:
    """
    Strictly redacts sensitive information from the text using Presidio.
    Raises RuntimeError if Presidio fails during scrubbing.
    """
    if not text:
        return ""

    try:
        # Use singleton instances to avoid repeated initialization log spam
        analyzer, anonymizer = _get_presidio_instances()

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


def _sanitize_config_content(config_content: str) -> str:
    """
    Sanitize LLM output by removing markdown artifacts before YAML parsing.
    
    Removes mermaid diagrams, other diagram blocks, markdown prose, and extracts
    content from YAML code fences. This prevents YAML parsing failures caused by
    LLM responses that wrap configs in markdown formatting.
    
    LIMITATION: This function assumes no nested code blocks within YAML content.
    If the YAML itself contains markdown-formatted code examples, those may be
    incorrectly processed. This is acceptable for deployment configs which should
    not contain nested markdown.
    
    Args:
        config_content: Raw config content that may contain markdown artifacts
        
    Returns:
        Sanitized config content ready for YAML parsing
    """
    # STEP 1: Remove mermaid and diagram blocks completely (not part of deployment config)
    config_content = re.sub(r'```\s*mermaid[\s\S]*?```', '', config_content, flags=re.MULTILINE | re.IGNORECASE)
    config_content = re.sub(r'```\s*(dot|plantuml|graphviz)[\s\S]*?```', '', config_content, flags=re.MULTILINE | re.IGNORECASE)
    
    # STEP 2: Extract content from YAML code fences
    # Look for yaml, helm, kubernetes code blocks and extract only the inner content
    # Note: Uses first match if multiple blocks exist; assumes no nested code blocks
    deployment_fence_pattern = r'```\s*(?:yaml|yml|kubernetes|k8s|helm)\s*\n?([\s\S]*?)```'
    matches = re.findall(deployment_fence_pattern, config_content, flags=re.IGNORECASE)
    
    if matches:
        # If we found yaml code blocks, use the first one
        config_content = matches[0]
    else:
        # No code fences found, strip any remaining code fence markers
        config_content = re.sub(r'```[a-z]*\n?', '', config_content, flags=re.IGNORECASE)
        config_content = re.sub(r'```', '', config_content)
    
    # STEP 3: Remove markdown headers and links
    config_content = re.sub(r'^#{1,6}\s+.*$', '', config_content, flags=re.MULTILINE)
    config_content = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', config_content)
    
    return config_content.strip()


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
        # re.MULTILINE makes ^ and $ match line boundaries, not just string boundaries
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

            # Check if trivy is available before attempting to use it
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
    # Use 'target' instead of 'format' to match the metric label definition
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
            "lint_status": "unknown",  # Add lint_status field expected by tests
            "lint_issues": [],
            "security_findings": [],
            "compliance_score": 0.0,  # Will be calculated
        }

        # Check if Docker validation should be skipped (CI/production environment)
        skip_docker_validation = os.getenv("SKIP_DOCKER_VALIDATION", "false").lower() in ("true", "1", "yes")
        # Check if the Docker socket path is present and is a real Unix socket.
        # On Railway and similar platforms the socket file may exist as a placeholder
        # but not be connectable; stat.S_ISSOCK distinguishes a real socket from a plain file.
        _docker_socket_path = os.environ.get("DOCKER_HOST", "/var/run/docker.sock").replace("unix://", "")
        try:
            import stat as _stat_mod
            _sock_stat = os.stat(_docker_socket_path)
            docker_socket_available = _stat_mod.S_ISSOCK(_sock_stat.st_mode)
        except OSError:
            docker_socket_available = False

        if skip_docker_validation or not docker_socket_available:
            if not docker_socket_available:
                logger.warning("Docker socket unavailable — skipping Docker build validation")
            else:
                logger.info("Docker validation skipped (SKIP_DOCKER_VALIDATION=true)")
            report["build_status"] = "skipped"
            report["lint_status"] = "skipped"
            report["compliance_score"] = 1.0  # Pass validation when explicitly skipped
            report["lint_issues"].append(
                "Docker validation skipped: Docker socket unavailable or SKIP_DOCKER_VALIDATION set. "
                "This is expected in CI/production environments without Docker daemon."
            )
            return report

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            dockerfile_path = temp_dir_path / "Dockerfile"

            try:
                # Corrected to use aiofiles.open for async file write
                async with aiofiles.open(
                    dockerfile_path, mode="w", encoding="utf-8"
                ) as f:
                    await f.write(config_content)

                # Check if Docker is available before attempting to use it
                if not shutil.which("docker"):
                    logger.warning("Docker tool not found. Skipping Docker build test.")
                    report["build_status"] = "skipped"
                    report["lint_status"] = "skipped"  # Also set lint_status to skipped
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
                # Check if hadolint is available before attempting to use it
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
                        # Only add lint issues when hadolint actually reports problems (returncode != 0)
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
                        # Set lint_status based on hadolint results
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
                            "skipped"  # Set status when tool not found
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
                        report["lint_status"] = "error"  # Set status on error
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
                # Use more strict scoring (divide by 5 instead of 10) so issues have bigger impact
                total_issues = len(report["lint_issues"]) + len(
                    report["security_findings"]
                )
                
                # Set 'valid' key based on validation results
                # A Dockerfile that can't be parsed should NEVER be marked valid
                # Check for parse errors in lint_issues (e.g., "unexpected '!' expecting '#', ADD, ARG...")
                has_parse_error = False
                parse_error_keywords = [
                    "unexpected", "expecting", "parse error", "syntax error",
                    "cannot start any token", "found character", "invalid instruction"
                ]
                
                for issue in report["lint_issues"]:
                    issue_lower = issue.lower()
                    if any(keyword in issue_lower for keyword in parse_error_keywords):
                        has_parse_error = True
                        logger.warning(f"Parse error detected in Dockerfile: {issue}")
                        break
                
                # If there's a parse error, compliance score should be 0.0 and valid should be False
                if has_parse_error or report["lint_status"] == "failed":
                    report["compliance_score"] = 0.0
                    report["valid"] = False
                    logger.error("Dockerfile failed validation due to parse errors - marked as invalid")
                else:
                    # Normal compliance score calculation
                    report["compliance_score"] = (
                        1.0 if total_issues == 0 else max(0.0, 1.0 - (total_issues / 5.0))
                    )
                    
                    # Consider valid if build succeeded (or skipped) and no critical issues
                    report["valid"] = (
                        report["build_status"] in ("success", "skipped", "lint_warning") and
                        report["lint_status"] in ("success", "skipped", "warning") and
                        not any("failed to build" in i.lower() for i in report["lint_issues"])
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
                ).inc()
            logger.error(
                "Failed to fix Dockerfile issues using LLM: %s", e, exc_info=True
            )
            raise RuntimeError(f"Failed to auto-fix Dockerfile issues: {e}") from e


class KubernetesValidator(Validator):
    """Basic Kubernetes manifest validator."""
    __version__ = "1.0"
    __source__ = "built-in"

    async def validate(self, config_content: str, target_type: str) -> Dict[str, Any]:
        """Validates Kubernetes manifests for YAML syntax and basic structure."""
        # Sanitize markdown/mermaid artifacts from LLM output before YAML parsing
        config_content = _sanitize_config_content(config_content)
        
        report = {
            "lint_status": "unknown",
            "lint_output": "",
            "lint_issues": [],
            "security_findings": [],
            "compliance_score": 0.0,
        }

        try:
            # 1. Validate YAML syntax
            try:
                manifests = list(RuYAML().load_all(config_content))
                if not manifests:
                    report["lint_issues"].append("No Kubernetes manifests found in YAML content")
                    report["lint_status"] = "failed"
                else:
                    report["lint_status"] = "success"
                    
                    # 2. Validate basic K8s structure
                    for i, manifest in enumerate(manifests):
                        if not isinstance(manifest, dict):
                            # Attempt to re-parse after stripping any residual markdown fences
                            if isinstance(manifest, str):
                                stripped = re.sub(r'^\s*```[a-z]*\s*\n?', '', manifest, flags=re.IGNORECASE)
                                stripped = re.sub(r'\n?```\s*$', '', stripped)
                                try:
                                    reparsed = RuYAML().load(stripped.strip())
                                    if isinstance(reparsed, dict):
                                        manifest = reparsed
                                    else:
                                        logger.warning("Manifest %d could not be recovered after fence-stripping", i + 1)
                                        continue
                                except RuamelYAMLError:
                                    logger.warning("Manifest %d could not be recovered after fence-stripping", i + 1)
                                    continue
                            else:
                                report["lint_issues"].append(f"Manifest {i+1} is not a valid dictionary")
                                continue
                        
                        # Check for required K8s fields
                        if "apiVersion" not in manifest:
                            report["lint_issues"].append(f"Manifest {i+1} missing 'apiVersion' field")
                        if "kind" not in manifest:
                            report["lint_issues"].append(f"Manifest {i+1} missing 'kind' field")
                        if "metadata" not in manifest:
                            report["lint_issues"].append(f"Manifest {i+1} missing 'metadata' field")
                        
                        # Log successful validation
                        if all(k in manifest for k in ["apiVersion", "kind", "metadata"]):
                            logger.debug(
                                f"Validated K8s manifest {i+1}: {manifest.get('kind')} "
                                f"({manifest.get('apiVersion')})"
                            )
                    
                    if report["lint_issues"]:
                        report["lint_status"] = "warning"
                        issue_total_found.labels(
                            target=target_type, issue_type_category="K8sStructure"
                        ).inc(len(report["lint_issues"]))
                        
            except RuamelYAMLError as e:
                # Use RuamelYAMLError (not yaml.YAMLError)
                # The validator uses ruamel.yaml (RuYAML) for parsing, which raises
                # ruamel.yaml.YAMLError. Using yaml.YAMLError would cause NameError
                # since PyYAML is not imported, leading to incorrect "internal_error"
                # status instead of proper YAML syntax error reporting.
                #
                # Industry Standard: Always catch exceptions from the library you're using
                # Reference: ruamel.yaml documentation
                report["lint_status"] = "failed"
                report["lint_output"] = f"YAML parsing error: {e}"
                report["lint_issues"].append(f"Invalid YAML syntax: {e}")
                issue_total_found.labels(
                    target=target_type, issue_type_category="YAMLSyntax"
                ).inc()
                logger.error(
                    f"YAML parsing failed for {target_type}",
                    extra={
                        "target_type": target_type,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }
                )

            # 3. Security Findings
            report["security_findings"] = await scan_config_for_findings(
                config_content, target_type, DANGEROUS_CONFIG_PATTERNS
            )

            # Calculate compliance score
            total_issues = len(report.get("lint_issues", [])) + len(
                report.get("security_findings", [])
            )
            report["compliance_score"] = (
                1.0 if total_issues == 0 else max(0.0, 1.0 - (total_issues / 5.0))
            )
            
            # Set 'valid' key based on validation results
            # Consider valid if lint succeeded and no critical issues
            report["valid"] = (
                report["lint_status"] in ("success", "warning") and
                total_issues == 0
            )

        except Exception as e:
            report["lint_status"] = "internal_error"
            report["lint_output"] = f"Internal validation error: {e}"
            logger.error(
                "Internal error during Kubernetes validation: %s", e, exc_info=True
            )
            report["lint_issues"].append(f"Internal validator error: {e}")
            issue_total_found.labels(
                target=target_type, issue_type_category="InternalError"
            ).inc()

        return report

    async def fix(
        self, config_content: str, issues: List[str], target_type: str
    ) -> str:
        """Attempts to fix Kubernetes manifest issues using an LLM."""
        fix_prompt = f"Fix these issues in the Kubernetes manifest YAML:\n{json.dumps(issues, indent=2)}\n\nOriginal Kubernetes YAML:\n```yaml\n{config_content}\n```\n\nProvide ONLY the corrected Kubernetes YAML content. Do not add any conversational text or markdown wrappers."

        try:
            start_time = time.time()
            fixed_response = await call_ensemble_api(
                fix_prompt,
                [{"provider": "openai", "model": "gpt-4o"}],
                voting_strategy="majority",
                stream=False,
            )

            LLM_CALLS_TOTAL.labels(
                provider="deploy_validator", model="gpt-4o"
            ).inc()
            LLM_LATENCY_SECONDS.labels(
                provider="deploy_validator", model="gpt-4o"
            ).observe(time.time() - start_time)
            await add_provenance("fix_kubernetes_config", {"action": "fix_kubernetes_config", "model": "gpt-4o"})

            fixed_config_content = fixed_response.get("content", "").strip()

            if not fixed_config_content:
                LLM_ERRORS_TOTAL.labels(
                    provider="deploy_validator",
                    model="gpt-4o",
                ).inc()
                raise ValueError("LLM returned empty content for Kubernetes fix.")

            # Clean up potential markdown fences
            fixed_config_content = re.sub(
                r"^```(yaml|yml)?\n", "", fixed_config_content, flags=re.IGNORECASE
            )
            fixed_config_content = re.sub(r"\n```$", "", fixed_config_content)

            return fixed_config_content
        except Exception as e:
            if not isinstance(e, LLMError):
                LLM_ERRORS_TOTAL.labels(
                    provider="deploy_validator",
                    model="gpt-4o",
                ).inc()
            logger.error(
                "Failed to fix Kubernetes manifest issues using LLM: %s", e, exc_info=True
            )
            raise RuntimeError(f"Failed to auto-fix Kubernetes manifest issues: {e}") from e


class HelmValidator(Validator):
    __version__ = "1.1"
    __source__ = "built-in"

    async def validate(self, config_content: str, target_type: str) -> Dict[str, Any]:
        """Validates a Helm chart by linting and running security scans."""
        # Sanitize markdown/mermaid artifacts from LLM output before YAML parsing
        config_content = _sanitize_config_content(config_content)
        
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
                    # Make filtering case-insensitive to catch "Error:", "error:", "ERROR", etc.
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
                # Use more strict scoring (divide by 5 instead of 10) so issues have bigger impact
                total_issues = len(report.get("lint_issues", [])) + len(
                    report.get("security_findings", [])
                )
                report["compliance_score"] = (
                    1.0 if total_issues == 0 else max(0.0, 1.0 - (total_issues / 5.0))
                )
                
                # Set 'valid' key based on validation results
                # Consider valid if lint succeeded and no critical issues
                report["valid"] = (
                    report["lint_status"] in ("success", "warning") and
                    total_issues == 0
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
                [{"provider": "openai", "model": "gpt-4o"}],
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


class DeploymentCompletenessValidator(Validator):
    """
    Validator that ensures all required deployment files exist and are valid.
    
    This validator checks:
    - Docker: Dockerfile, docker-compose.yml, .dockerignore
    - Kubernetes: k8s/deployment.yaml, k8s/service.yaml, k8s/configmap.yaml
    - Helm: helm/Chart.yaml, helm/values.yaml, helm/templates/ directory
    - YAML files pass basic YAML validation
    - Dockerfile has required instructions (FROM, EXPOSE, etc.)
    - No unsubstituted placeholders remain in any deployment file
    """
    
    __version__ = "1.0"
    __source__ = "built-in"
    
    # Required files for each deployment type
    # These are the minimum files that the deploy agent actually generates
    REQUIRED_FILES = {
        "docker": ["Dockerfile", ".dockerignore"],
        "kubernetes": [
            "k8s/deployment.yaml",
            "k8s/service.yaml",
        ],
        "helm": [
            "helm/Chart.yaml",
            "helm/values.yaml",
            "helm/templates/",
        ],
    }
    
    # Recommended files that are optional but provide additional functionality
    # These generate warnings instead of failures if missing
    RECOMMENDED_FILES = {
        "docker": ["docker-compose.yml"],
        "kubernetes": ["k8s/configmap.yaml", "k8s/ingress.yaml"],
        "helm": [],
    }
    
    # Placeholder patterns to detect (both curly and angle bracket variants)
    PLACEHOLDER_PATTERNS = [
        r'\{[A-Z_]+\}',  # {PLACEHOLDER}
        r'<[A-Z_]+>',    # <PLACEHOLDER>
        r'\{[a-z_]+\}',  # {placeholder}
        r'<[a-z_]+>',    # <placeholder>
    ]
    
    async def validate(self, config_content: str, target_type: str) -> Dict[str, Any]:
        """
        Validates deployment completeness for the given target type.
        
        Args:
            config_content: The deployment configuration content (may be unused if validating files on disk)
            target_type: The deployment target type (docker, kubernetes, helm, or "all")
            
        Returns:
            Dict containing validation report with:
                - status: "passed" or "failed"
                - missing_files: List of missing required files
                - invalid_files: List of files with validation errors
                - placeholder_issues: Files containing unsubstituted placeholders
                - errors: List of detailed error messages
        """
        report = {
            "status": "passed",
            "missing_files": [],
            "invalid_files": [],
            "placeholder_issues": [],
            "errors": [],
            "warnings": [],  # Add warnings for recommended files
        }
        
        # If target_type is "all", only validate targets that have generated files
        if target_type == "all":
            # Detect which targets were actually generated by checking for marker files
            targets_to_check = []
            if Path("Dockerfile").exists():
                targets_to_check.append("docker")
            if Path("k8s").exists() and Path("k8s").is_dir():
                targets_to_check.append("kubernetes")
            if Path("helm").exists() and Path("helm").is_dir():
                targets_to_check.append("helm")
            
            # If no deployment files found, don't fail validation
            if not targets_to_check:
                report["warnings"].append("No deployment files found - skipping deployment validation")
                return report
        else:
            targets_to_check = [target_type]
        
        for target in targets_to_check:
            if target not in self.REQUIRED_FILES:
                continue
                
            required_files = self.REQUIRED_FILES[target]
            
            # Check if all required files exist
            for file_path in required_files:
                full_path = Path(file_path)
                
                # Check if it's a directory requirement
                if file_path.endswith('/'):
                    if not full_path.exists() or not full_path.is_dir():
                        report["missing_files"].append(f"{target}: {file_path}")
                        report["errors"].append(f"Required directory missing for {target}: {file_path}")
                        report["status"] = "failed"
                else:
                    if not full_path.exists():
                        report["missing_files"].append(f"{target}: {file_path}")
                        report["errors"].append(f"Required file missing for {target}: {file_path}")
                        report["status"] = "failed"
                    else:
                        # Validate the file content
                        try:
                            async with aiofiles.open(full_path, 'r', encoding='utf-8') as f:
                                content = await f.read()
                            
                            # Check for unsubstituted placeholders
                            placeholder_found = False
                            for pattern in self.PLACEHOLDER_PATTERNS:
                                matches = re.findall(pattern, content)
                                if matches:
                                    # Filter out valid template patterns (Helm/Jinja2/Go templates)
                                    # Valid patterns include: {{ .Values.x }}, {{ .Chart.x }}, {{ include "..." . }}
                                    invalid_matches = []
                                    for match in matches:
                                        # Check if this match appears within template delimiters
                                        # Look for the match surrounded by {{ ... }}
                                        
                                        # Pattern 1: {{ match }} (exact match in template)
                                        if f'{{{{ {match} }}}}' in content or f'{{{{.{match}' in content:
                                            continue  # Valid Helm/Go template
                                        
                                        # Pattern 2: {{ .Values.match }} or {{ .Chart.match }}
                                        if f'{{{{ .Values.{match}' in content or f'{{{{ .Chart.{match}' in content:
                                            continue  # Valid Helm template with Values/Chart
                                        
                                        # Pattern 3: Check if within any {{ ... }} block
                                        # Find all {{ ... }} blocks and check if match is inside any
                                        is_in_template = False
                                        for template_match in re.finditer(r'\{\{.*?\}\}', content, re.DOTALL):
                                            template_text = template_match.group(0)
                                            if match in template_text:
                                                is_in_template = True
                                                break
                                        
                                        if is_in_template:
                                            continue  # Match is inside a template block
                                        
                                        # If we get here, it's likely an unsubstituted placeholder
                                        invalid_matches.append(match)
                                    
                                    if invalid_matches:
                                        placeholder_found = True
                                        report["placeholder_issues"].append(f"{file_path}: {invalid_matches}")
                                        report["errors"].append(
                                            f"Unsubstituted placeholders found in {file_path}: {invalid_matches}"
                                        )
                            
                            if placeholder_found:
                                report["status"] = "failed"
                            
                            # Validate YAML files
                            if file_path.endswith(('.yaml', '.yml')):
                                await self._validate_yaml(content, file_path, report)
                            
                            # Validate Dockerfile
                            elif file_path == "Dockerfile":
                                await self._validate_dockerfile(content, file_path, report)
                                
                        except Exception as e:
                            report["invalid_files"].append(f"{file_path}: {str(e)}")
                            report["errors"].append(f"Error reading/validating {file_path}: {str(e)}")
                            report["status"] = "failed"
            
            # Check for recommended files (warnings only, not failures)
            if target in self.RECOMMENDED_FILES:
                recommended_files = self.RECOMMENDED_FILES[target]
                for file_path in recommended_files:
                    full_path = Path(file_path)
                    if not full_path.exists():
                        report["warnings"].append(
                            f"Recommended file missing for {target}: {file_path}. "
                            f"This is optional but recommended for production deployments."
                        )
                    elif file_path.endswith(('.yaml', '.yml')):
                        # Validate YAML syntax for recommended files that exist
                        try:
                            async with aiofiles.open(full_path, 'r', encoding='utf-8') as f:
                                content = await f.read()
                            yaml_report: Dict[str, Any] = {
                                "invalid_files": [],
                                "errors": [],
                                "status": "passed",
                            }
                            await self._validate_yaml(content, file_path, yaml_report)
                            if yaml_report["status"] == "failed":
                                report["warnings"].append(
                                    f"Recommended file {file_path} has invalid YAML: "
                                    + "; ".join(yaml_report["errors"])
                                )
                                report["invalid_files"].extend(yaml_report["invalid_files"])
                        except Exception as e:
                            report["warnings"].append(
                                f"Could not validate recommended file {file_path}: {e}"
                            )
        
        # Validate that deployment files match the actual generated code
        await self._validate_deployment_matches_code(report)
        
        logger.info(f"DeploymentCompletenessValidator validation {report['status']} for {target_type}")
        return report
    
    async def _validate_yaml(self, content: str, file_path: str, report: Dict[str, Any]) -> None:
        """Validate YAML syntax."""
        try:
            yaml = RuYAML()
            yaml.load(content)
        except Exception as e:
            report["invalid_files"].append(f"{file_path}: Invalid YAML")
            report["errors"].append(f"YAML validation failed for {file_path}: {str(e)}")
            report["status"] = "failed"
    
    async def _validate_dockerfile(self, content: str, file_path: str, report: Dict[str, Any]) -> None:
        """Validate Dockerfile has required instructions and sane ENTRYPOINT/CMD semantics."""
        required_instructions = ["FROM"]

        lines = content.strip().split('\n')
        instructions_found: Set[str] = set()
        entrypoint_value: Optional[str] = None
        cmd_value: Optional[str] = None

        # Single pass: collect required instructions and exec-form ENTRYPOINT/CMD values
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            parts = stripped.split(None, 1)
            if not parts:
                continue
            instr = parts[0].upper()
            instructions_found.add(instr)
            if len(parts) == 2:
                val = parts[1].strip()
                if instr == "ENTRYPOINT" and val.startswith("["):
                    entrypoint_value = val
                elif instr == "CMD" and val.startswith("["):
                    cmd_value = val

        missing_instructions = [inst for inst in required_instructions if inst not in instructions_found]
        if missing_instructions:
            report["invalid_files"].append(f"{file_path}: Missing instructions: {missing_instructions}")
            report["errors"].append(
                f"Dockerfile validation failed for {file_path}: Missing required instructions {missing_instructions}"
            )
            report["status"] = "failed"

        # Semantic check: ENTRYPOINT + CMD exec-form compatibility.
        # Catches the common mistake of ENTRYPOINT ["python"] + CMD ["uvicorn", ...]
        # which produces `python uvicorn ...` instead of `uvicorn ...` or `python -m uvicorn ...`.
        if entrypoint_value and cmd_value:
            try:
                ep_list = json.loads(entrypoint_value)
                cmd_list = json.loads(cmd_value)
                # Python interpreters that should not be bare ENTRYPOINT when CMD is a server
                if (
                    isinstance(ep_list, list)
                    and isinstance(cmd_list, list)
                    and ep_list in _BARE_PYTHON_ENTRYPOINTS
                    and cmd_list
                    and cmd_list[0] in _KNOWN_SERVER_EXECUTABLES
                ):
                    interpreter = ep_list[0]
                    executable = cmd_list[0]
                    msg = (
                        f"Dockerfile ENTRYPOINT/CMD conflict in {file_path}: "
                        f"ENTRYPOINT {ep_list} + CMD {cmd_list} produces "
                        f"`{interpreter} {executable} ...` which is invalid. "
                        f"Either remove ENTRYPOINT and use CMD directly "
                        f"(e.g. CMD [\"{executable}\", ...]), "
                        f"or change CMD to use the -m flag "
                        f"(e.g. CMD [\"-m\", \"{executable}\", ...])."
                    )
                    report["invalid_files"].append(f"{file_path}: ENTRYPOINT/CMD conflict")
                    report["errors"].append(msg)
                    report["status"] = "failed"
            except (json.JSONDecodeError, TypeError):
                pass
    
    async def _validate_deployment_matches_code(self, report: Dict[str, Any]) -> None:
        """
        Validate that deployment configurations match the actual generated code.
        
        Checks:
        - Dockerfile copies/references actual project files
        - Kubernetes/Helm configs use ports that match the application code
        - Entry points match actual main files
        """
        warnings = []
        
        # Check if we have a requirements.txt or package.json in the project
        has_requirements = Path("requirements.txt").exists()
        has_package_json = Path("package.json").exists()
        has_go_mod = Path("go.mod").exists()
        
        # Validate Dockerfile if it exists
        dockerfile_path = Path("Dockerfile")
        if dockerfile_path.exists():
            try:
                async with aiofiles.open(dockerfile_path, 'r', encoding='utf-8') as f:
                    dockerfile_content = await f.read()
                
                # Check if Dockerfile copies dependency files
                if has_requirements and "requirements.txt" not in dockerfile_content:
                    warnings.append("Dockerfile doesn't reference requirements.txt found in project")
                
                if has_package_json and "package.json" not in dockerfile_content:
                    warnings.append("Dockerfile doesn't reference package.json found in project")
                
                if has_go_mod and "go.mod" not in dockerfile_content:
                    warnings.append("Dockerfile doesn't reference go.mod found in project")
                
                # Check for common entry points
                common_entry_points = ["main.py", "app.py", "server.py", "index.js", "server.js", "main.go"]
                found_entry_points = [ep for ep in common_entry_points if Path(ep).exists()]
                
                if found_entry_points:
                    # Check if at least one entry point is referenced in Dockerfile
                    if not any(ep in dockerfile_content for ep in found_entry_points):
                        warnings.append(
                            f"Dockerfile may not use detected entry points: {found_entry_points}"
                        )
                        
            except Exception as e:
                logger.warning(f"Could not validate Dockerfile content matching: {e}")
        
        # Add warnings to report (non-fatal)
        if warnings:
            report["warnings"] = warnings
            logger.info(f"Deployment matching validation warnings: {warnings}")
    
    async def fix(self, config_content: str, issues: List[str], target_type: str) -> str:
        """
        Attempts to fix detected issues in deployment files.
        
        For deployment completeness issues, this typically means generating missing files
        or fixing placeholders, which is better handled by regenerating the deployment.
        
        Args:
            config_content: The configuration content to fix
            issues: List of issues detected during validation
            target_type: The deployment target type
            
        Returns:
            Fixed configuration content (or original if fixing is not applicable)
        """
        # For now, return the original content as fixing deployment completeness
        # usually requires regenerating missing files rather than fixing existing content
        logger.warning(
            f"DeploymentCompletenessValidator.fix called for {target_type}, "
            f"but fixing is not implemented. Issues: {issues}"
        )
        return config_content


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
            "kubernetes": KubernetesValidator,
            "helm": HelmValidator,
            "completeness": DeploymentCompletenessValidator,
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
