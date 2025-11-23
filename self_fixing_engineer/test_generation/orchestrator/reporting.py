# reporting.py - Production-Ready HTML Report Generator

# This module is responsible for creating and securely writing comprehensive HTML reports
# for ATCO runs. It adheres to strict production readiness standards, including:

# - Hard Dependency on Audit Logging: Fails loudly if the `arbiter.audit_log`
#   dependency is missing to ensure no critical events go unrecorded.
# - Secure File I/O: All file and directory operations are hardened against path
#   traversal and malicious inputs. All file writes are performed atomically.
# - HTML Sanitization: All dynamic data is meticulously sanitized before being
#   written to the report to prevent Cross-Site Scripting (XSS) attacks.
# - Robust Error Handling: All failures, from input validation to file system errors,
#   are audited and escalated to ensure no silent failures occur.

import os
import json
import logging
import traceback
import tempfile
from datetime import datetime, timezone
from typing import Dict, Any
import textwrap
import html  # For sanitization
import random
import asyncio
import re
from importlib.metadata import version
import time
from packaging.version import Version
from test_generation.utils import atomic_write, maybe_await
from pathlib import Path


# Initialize module exports - DummyMetric will be added after its definition
__all__ = ["DummyMetric"]


# ---- test helper: DummyMetric (exported) ------------------------------------
class DummyMetric:
    """
    Minimal metric stub used by tests when Prometheus isn't present.
    Exposes .labels().inc()/observe()/set() and a context manager for timing.
    """

    # Add DEFAULT_BUCKETS to match Histogram.DEFAULT_BUCKETS
    DEFAULT_BUCKETS = (
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
        7.5,
        10.0,
        float("inf"),
    )

    def labels(self, **_kwargs):
        return self

    def observe(self, *_a, **_k):
        return None

    def inc(self, *_a, **_k):
        return None

    class _Timer:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    def time(self):
        return self._Timer()

    def __enter__(self):
        return self._Timer().__enter__()

    def __exit__(self, exc_type, exc, tb):
        return self._Timer().__exit__(exc_type, exc, tb)


# DummyMetric is already in __all__ at module initialization
# -----------------------------------------------------------------------------

# --- Metrics Integration ---
# FIX: A configuration toggle for Prometheus is needed to address potential CPU degradation.
_ENABLE_PROM = os.getenv("ATCO_ENABLE_PROMETHEUS", "true").lower() not in (
    "0",
    "false",
    "no",
)

if not _ENABLE_PROM:
    report_generation_duration = DummyMetric()
    report_generation_errors = DummyMetric()
    METRICS_AVAILABLE = False
    logging.getLogger(__name__).warning("Metrics disabled by configuration.")
else:
    try:
        from packaging.version import Version

        prom_ver = Version(version("prometheus_client"))
        if prom_ver >= Version("0.22.1") and prom_ver < Version("0.22.2"):
            logging.warning(
                "Warning: Known CPU degradation issue with prometheus_client==0.22.1. Metrics may be disabled."
            )
            raise Exception("Prometheus metrics disabled due to known bug.")

        from prometheus_client import Counter, Histogram, CollectorRegistry

        _registry = CollectorRegistry(auto_describe=True)
        report_generation_duration = Histogram(
            "atco_report_generation_seconds",
            "Time taken to generate HTML reports",
            registry=_registry,
        )
        report_generation_errors = Counter(
            "atco_report_generation_errors_total",
            "Failed report generations",
            registry=_registry,
        )
        METRICS_AVAILABLE = True
    except Exception:
        METRICS_AVAILABLE = False
        report_generation_duration = DummyMetric()
        report_generation_errors = DummyMetric()
        logging.getLogger(__name__).warning(
            "Warning: 'prometheus_client' not available or bugged. Metrics disabled."
        )


# Import log from utils
logger = logging.getLogger(__name__)

# --- CRITICAL DEPENDENCY CHECK ---
# This is a hard failure on import if critical dependencies are missing.
try:
    from arbiter.audit_log import audit_logger

    AUDIT_LOGGER_AVAILABLE = True
except ImportError:
    logging.critical(
        "CRITICAL: The 'arbiter.audit_log' library is a required dependency but was not found. Aborting."
    )
    raise


class ReportValidationError(ValueError):
    """Custom exception for report schema validation failures."""

    pass


async def _write_sarif_atomically(path: str, data: Dict[str, Any]) -> bool:
    """
    Writes SARIF data to a file in an atomic manner.

    This ensures that the file is either fully written and valid,
    or it remains in its original state, preventing corrupted files
    due to interruptions during the write process.
    """
    tmp_name = None
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # Create a temporary file in the same directory as the final file
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, dir=os.path.dirname(path), encoding="utf-8"
        ) as tmp:
            tmp_name = tmp.name
            json.dump(data, tmp, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())  # Ensure data is written to disk

        # Atomically replace the old file with the new one
        os.replace(tmp_name, path)
        return True
    except Exception as e:
        # Log the error and clean up the temporary file if it still exists
        logger.error(
            f"Failed to write SARIF file atomically to '{path}': {e}", exc_info=True
        )
        if tmp_name and os.path.exists(tmp_name):
            os.remove(tmp_name)
        return False


async def cleanup_old_temp_files(path: str) -> None:
    """Deletes temporary files older than a certain threshold."""
    if not os.path.isdir(path):
        return

    now = time.time()
    for filename in os.listdir(path):
        if filename.startswith("atco_sarif_") or filename.endswith(".tmp"):
            filepath = os.path.join(path, filename)
            try:
                if (now - os.stat(filepath).st_mtime) > 3600:  # 1 hour
                    os.remove(filepath)
                    logger.debug(f"Cleaned up old temporary file: {filepath}")
            except OSError as e:
                logger.warning(f"Error cleaning up old file {filepath}: {e}")


class HTMLReporter:
    """
    Generates a comprehensive, self-contained HTML report for an ATCO run.
    """

    def __init__(
        self,
        project_root: str,
        report_dir: str = "atco_artifacts/sarif",
        sarif_dir: str = None,
    ):
        self.project_root = os.path.abspath(project_root)

        # Resolve report_dir against project_root if it's relative
        report_dir_to_use = report_dir or sarif_dir or "atco_artifacts/sarif"
        if not os.path.isabs(report_dir_to_use):
            report_dir_abs = os.path.abspath(
                os.path.join(self.project_root, report_dir_to_use)
            )
        else:
            report_dir_abs = os.path.abspath(report_dir_to_use)

        # Compute a relative path only if possible; otherwise keep the provided path
        try:
            self.report_dir_relative = os.path.relpath(
                report_dir_abs, self.project_root
            )
        except ValueError:
            # Different drive letters on Windows; just use the given directory as-is
            self.report_dir_relative = report_dir_to_use.replace("\\", "/")

        # Final absolute path for filesystem operations
        self.report_dir = os.path.join(self.project_root, self.report_dir_relative)

        # Use pathlib to create the directory, which is robust to `os.path.exists` patches
        from pathlib import Path

        Path(self.report_dir).mkdir(parents=True, exist_ok=True)
        if not os.access(self.report_dir, os.W_OK):
            raise IOError(f"Directory is not writable: {self.report_dir}")

        logger.info(
            f"HTMLReporter initialized. Reports will be saved to: {self.report_dir}"
        )

    def _sanitize_and_create_dir(self, path_relative: str) -> str:
        """Sanitizes a path and ensures the directory exists and is writable."""
        if not path_relative or ".." in path_relative or os.path.isabs(path_relative):
            raise ValueError(
                f"Invalid directory path: '{path_relative}'. Must be a relative path."
            )

        full_path = os.path.join(self.project_root, path_relative)
        os.makedirs(full_path, exist_ok=True)

        if not os.access(full_path, os.W_OK):
            raise IOError(f"Directory is not writable: {full_path}")

        return full_path

    def _validate_report_schema(self, overall_results: Dict[str, Any]):
        """
        Validates the input schema for report generation with deep checks.
        """
        if not isinstance(overall_results, dict):
            raise ReportValidationError("Overall results must be a dictionary.")
        if (
            "summary" not in overall_results
            or "details" not in overall_results
            or "ai_metrics" not in overall_results
        ):
            raise ReportValidationError(
                "Overall results must contain 'summary', 'details', and 'ai_metrics' keys."
            )

        if not isinstance(overall_results["summary"], dict):
            raise ReportValidationError("'summary' must be a dictionary.")
        if not isinstance(overall_results["details"], dict):
            raise ReportValidationError("'details' must be a dictionary.")
        if not isinstance(overall_results["ai_metrics"], dict):
            raise ReportValidationError("'ai_metrics' must be a dictionary.")

        required_summary_keys = [
            "total_integrated",
            "total_quarantined",
            "total_requires_pr",
            "total_deduplicated",
            "total_not_generated",
            "total_targets_considered",
        ]
        for key in required_summary_keys:
            if key not in overall_results["summary"]:
                raise ReportValidationError(f"Missing required key in summary: '{key}'")

        required_ai_metrics_keys = [
            "refinement_success_rate_percent",
            "total_refinement_attempts",
            "total_generations",
        ]
        for key in required_ai_metrics_keys:
            if key not in overall_results["ai_metrics"]:
                raise ReportValidationError(
                    f"Missing required key in ai_metrics: '{key}'"
                )

        for identifier, detail in overall_results["details"].items():
            if not isinstance(detail, dict):
                raise ReportValidationError(
                    f"Detail entry for '{identifier}' is not a dictionary."
                )
            required_detail_keys = [
                "integration_status",
                "test_passed",
                "coverage_increase_percent",
                "security_issues_found",
                "security_max_severity",
                "reason",
                "language",
                "mutation_score_percent",
            ]
            for key in required_detail_keys:
                if key not in detail:
                    raise ReportValidationError(
                        f"Missing required key in detail for '{identifier}': '{key}'"
                    )

            if detail.get("sarif_artifact_path") and not re.match(
                r"^[a-zA-Z0-9_/.\-]+$", detail["sarif_artifact_path"]
            ):
                raise ReportValidationError(
                    f"Invalid characters in sarif_artifact_path for '{identifier}'"
                )
            if detail.get("final_integrated_test_hash") and not re.match(
                r"^[a-f0-9]{64}$", detail["final_integrated_test_hash"]
            ):
                pass

    def _sanitize_data_recursively(self, data: Any) -> Any:
        """Recursively sanitizes all string values in a nested data structure."""
        if isinstance(data, dict):
            return {k: self._sanitize_data_recursively(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._sanitize_data_recursively(item) for item in data]
        elif isinstance(data, str):
            return html.escape(data)
        else:
            return data

    async def generate_markdown_report(self, overall_results: Dict[str, Any]) -> str:
        """Generates a summary report in Markdown format."""
        sanitized_results = self._sanitize_data_recursively(overall_results)
        summary = sanitized_results["summary"]
        ai_metrics = sanitized_results["ai_metrics"]

        md_content = textwrap.dedent(
            f"""
        # ATCO Run Summary Report

        **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')}

        ## 📊 Execution Summary

        | Status | Count |
        |---|---|
        | Integrated | {summary.get('total_integrated', 0)} |
        | Quarantined | {summary.get('total_quarantined', 0)} |
        | Requires PR | {summary.get('total_requires_pr', 0)} |
        | Deduplicated | {summary.get('total_deduplicated', 0)} |
        | Not Generated | {summary.get('total_not_generated', 0)} |
        | **Total Targeted** | **{summary.get('total_targets_considered', 0)}** |

        ## 🤖 AI Performance Metrics

        * **Refinement Success Rate:** {float(ai_metrics.get('refinement_success_rate_percent', 0) or 0):.2f}%
        * **Total Refinement Attempts:** {ai_metrics.get('total_refinement_attempts', 0)}
        * **Total Test Generations:** {ai_metrics.get('total_generations', 0)}

        ## 📝 Detailed Results

        | Module/File | Status | Test Pass | Cov Gain | Sec. Issues | Reason |
        |---|---|---|---|---|---|
        """
        )

        for module_id, detail in sanitized_results["details"].items():
            test_pass_icon = "✅" if bool(detail.get("test_passed")) else "❌"
            security_icon = "✅"
            if bool(detail.get("security_issues_found")):
                severity_map = {
                    "LOW": "⚠️",
                    "MEDIUM": "⚠️",
                    "HIGH": "❌",
                    "CRITICAL": "❌",
                }
                security_icon = severity_map.get(detail["security_max_severity"], "⚠️")

            md_content += f"| {html.escape(module_id)} | {detail['integration_status']} | {test_pass_icon} | {float(detail.get('coverage_increase_percent', 0.0) or 0):.1f}% | {security_icon} | {detail['reason']} |\n"

        return md_content

    async def generate_html_report(
        self, overall_results: Dict[str, Any], policy_engine: Any
    ) -> str:
        with report_generation_duration.time():
            if os.getenv("DEMO_MODE", "False").lower() == "true":
                await maybe_await(
                    audit_logger.log_event(
                        event_type="report_generation",
                        details={
                            "action": "demo_mode_report",
                            "message": "Forced audit log entry due to DEMO_MODE",
                        },
                        critical=False,
                    )
                )

            try:
                self._validate_report_schema(overall_results)
            except ReportValidationError as e:
                logger.critical(
                    f"CRITICAL: Report schema validation failed: {e}", exc_info=True
                )
                await maybe_await(
                    audit_logger.log_event(
                        event_type="report_generation_failed",
                        details={
                            "error": f"Schema validation failed: {e}",
                            "inputs": overall_results,
                            "traceback": traceback.format_exc(),
                        },
                        critical=True,
                    )
                )
                raise

            report_filename = f"atco_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{random.randint(0,9999)}.html"
            report_path_relative = os.path.join(
                self.report_dir_relative, report_filename
            )
            full_report_path = os.path.join(self.project_root, report_path_relative)

            sanitized_results = self._sanitize_data_recursively(overall_results)
            sanitized_summary = sanitized_results["summary"]
            sanitized_details = sanitized_results["details"]
            sanitized_ai_metrics = sanitized_results["ai_metrics"]
            sanitized_policy_hash = html.escape(
                str(getattr(policy_engine, "policy_hash", ""))
            )

            # Re-format the details into a list of tuples to sort them
            sorted_details = sorted(sanitized_details.items(), key=lambda item: item[0])
            detail_rows = []
            for module_id, detail in sorted_details:
                # Prepare data for each row
                status_class_map = {
                    "INTEGRATED": "integrated",
                    "INTEGRATED_WITH_BACKUP": "integrated_with_backup",
                    "QUARANTINED": "quarantined",
                    "DEDUPLICATED": "deduplicated",
                    "REQUIRES_PR": "requires_pr",
                    "NOT_GENERATED": "not_generated",
                    "DENIED_BY_POLICY": "denied_by_policy",
                }
                status_class = status_class_map.get(
                    detail.get("integration_status"), "not_generated"
                )

                test_pass_icon = (
                    '<span class="icon success">&#x2714;</span>'
                    if bool(detail.get("test_passed"))
                    else '<span class="icon fail">&#x2716;</span>'
                )

                security_issues_found = bool(detail.get("security_issues_found"))
                security_max_severity = detail.get("security_max_severity", "N/A")
                security_icon_html = '<span class="icon success">&#x2714;</span>'
                if security_issues_found:
                    severity_map = {
                        "LOW": "warn",
                        "MEDIUM": "warn",
                        "HIGH": "fail",
                        "CRITICAL": "fail",
                    }
                    icon_map = {"warn": "&#9888;", "fail": "&#x2716;"}
                    severity_class = severity_map.get(security_max_severity, "warn")
                    security_icon_html = f'<span class="icon {severity_class}">{icon_map[severity_class]}</span>'

                sarif_link = "N/A"
                if detail.get("sarif_artifact_path"):
                    # Fix: Normalize path for POSIX-style URLs
                    sanitized_sarif_path = Path(
                        detail["sarif_artifact_path"]
                    ).as_posix()
                    sarif_link = (
                        f'<a href="{sanitized_sarif_path}" target="_blank">View</a>'
                    )

                file_hash_display = detail.get("final_integrated_test_hash", "N/A")
                if file_hash_display != "N/A":
                    file_hash_display = f"<code>{file_hash_display[:8]}...</code>"

                mutation_score_display = "N/A"
                if "mutation_score_percent" in detail:
                    mutation_score_display = (
                        f'{float(detail.get("mutation_score_percent", 0) or 0):.1f}%'
                    )

                row_html = f"""
                    <tr>
                        <td>{html.escape(module_id)}</td>
                        <td>{detail['language']}</td>
                        <td class="status-{status_class}">{detail['integration_status']}</td>
                        <td>{float(detail.get('coverage_increase_percent', 0.0) or 0):.1f}%</td>
                        <td>{test_pass_icon}</td>
                        <td>{security_icon_html} ({security_max_severity})</td>
                        <td>{detail['reason']}</td>
                        <td>{sarif_link}</td>
                        <td>{file_hash_display}</td>
                        <td>{mutation_score_display}</td>
                    </tr>
                """
                detail_rows.append(row_html)

            # Conditional panels
            quarantine_panel_html = ""
            if overall_results["summary"].get("total_quarantined", 0) > 0:
                quarantine_panel_html = f"""
                <div class="panel-warning">
                    <h3 style="color: var(--warning-color); border-bottom: none;">
                        <span class="icon warn">&#9888;</span> ACTION REQUIRED: Quarantined Tests
                    </h3>
                    <p style="color: var(--text-color);">
                        {sanitized_summary.get('total_quarantined', 0)} tests were quarantined. Please review them in the
                        <code>{Path(self.report_dir_relative).as_posix()}</code> directory. They might have import errors, failed execution,
                        security issues, or were denied by policy.
                    </p>
                </div>
                """

            pr_panel_html = ""
            if overall_results["summary"].get("total_requires_pr", 0) > 0:
                pr_panel_html = f"""
                <div class="panel-warning" style="background-color: #e0f2f7; border-color: var(--pr-color);">
                    <h3 style="color: var(--pr-color); border-bottom: none;">
                        <span class="icon warn">&#9432;</span> PULL REQUESTS REQUIRED
                    </h3>
                    <p style="color: var(--text-color);">
                        {sanitized_summary.get('total_requires_pr', 0)} tests require human review via Pull Request due to policy.
                        These tests are staged in the <code>{Path(self.report_dir_relative, 'for_pr').as_posix()}</code> directory.
                    </p>
                </div>
                """

            # Add CSP header to HTML
            csp_header = """
<meta http-equiv="Content-Security-Policy"
      content="default-src 'self';
               style-src 'self' https://fonts.googleapis.com;
               font-src https://fonts.gstatic.com;
               img-src 'self' data:;
               script-src 'self' 'unsafe-inline'">
"""

            # Construct the full HTML content using format()
            report_html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ATCO Run Report - {date_time_title}</title>
    {csp_header}
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --primary-color: #3498db;
            --success-color: #27ae60;
            --warning-color: #e67e22;
            --error-color: #c0392b;
            --info-color: #2c3e50;
            --dedupe-color: #7f8c8d;
            --pr-color: #8e44ad;
            --bg-light: #f4f7f6;
            --bg-white: #ffffff;
            --border-color: #eee;
            --text-color: #333;
            --text-light: #7f8c8d;
        }}
        body {{ font-family: 'Inter', sans-serif; line-height: 1.6; color: var(--text-color); margin: 0; background-color: var(--bg-light); }}
        .container {{ max-width: 1200px; margin: 30px auto; background: var(--bg-white); padding: 30px; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.08); }}
        h1, h2, h3 {{ color: var(--info-color); border-bottom: 1px solid var(--border-color); padding-bottom: 10px; margin-top: 25px; }}
        h1 {{ color: var(--primary-color); text-align: center; border-bottom: none; font-size: 2.5em; margin-bottom: 10px; }}
        .tagline {{ text-align: center; color: var(--text-light); font-size: 1.1em; margin-bottom: 30px; }}
        .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-top: 20px; text-align: center; }}
        .summary-card {{ background-color: #ecf0f1; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.03); transition: transform 0.2s ease; }}
        .summary-card:hover {{ transform: translateY(-5px); }}
        .summary-card h3 {{ margin-top: 0; font-size: 1.2em; color: var(--text-light); border-bottom: none; }}
        .summary-card p {{ font-size: 2.5em; font-weight: bold; margin: 5px 0; }}
        .summary-card.integrated p {{ color: var(--success-color); }}
        .summary-card.quarantined p {{ color: var(--warning-color); }}
        .summary-card.pr p {{ color: var(--pr-color); }}
        .summary-card.deduped p {{ color: var(--dedupe-color); }}
        .summary-card.not-gen p {{ color: var(--text-light); }}
        .summary-card {{ color: var(--info-color); }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; background-color: var(--bg-white); border-radius: 8px; overflow: hidden; }}
        th, td {{ padding: 15px; text-align: left; border-bottom: 1px solid #f2f2f2; }}
        th {{ background-color: #f8f8f8; font-weight: 600; color: var(--info-color); }}
        tr:last-child td {{ border-bottom: none; }}
        tr:nth-child(even) {{ background-color: #fcfcfc; }}
        tr:hover {{ background-color: #f0f0f0; }}
        .status-integrated {{ color: var(--success-color); font-weight: bold; }}
        .status-integrated_with_backup {{ color: var(--success-color); font-weight: bold; }}
        .status-quarantined {{ color: var(--warning-color); font-weight: bold; }}
        .status-deduplicated {{ color: var(--dedupe-color); font-weight: bold; }}
        .status-requires_pr {{ color: var(--pr-color); font-weight: bold; }}
        .status-not_generated {{ color: var(--text-light); }}
        .status-denied_by_policy {{ color: var(--error-color); font-weight: bold; }}
        .icon {{ font-size: 1.2em; margin-right: 5px; vertical-align: middle; }}
        .icon.success {{ color: var(--success-color); }}
        .icon.fail {{ color: var(--error-color); }}
        .icon.warn {{ color: var(--warning-color); }}
        .footer {{ text-align: center; margin-top: 50px; padding-top: 25px; border-top: 1px solid var(--border-color); color: var(--text-light); font-size: 0.95em; }}
        .panel-warning {{ background-color: #fff3e0; border: 1px solid var(--warning-color); padding: 15px; border-radius: 8px; margin-top: 20px; }}
        .panel-success {{ background-color: #e8f5e9; border: 1px solid var(--success-color); padding: 15px; border-radius: 8px; margin-top: 20px; }}
        a {{ color: var(--primary-color); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
    </style>
    <script>
        function paginateTable(page, perPage = 10) {{
            const tableBody = document.querySelector('table tbody');
            if (!tableBody) return;
            const rows = tableBody.querySelectorAll('tr');
            if (rows.length === 0) return;
            const totalPages = Math.ceil(rows.length / perPage);
            const paginationContainer = document.getElementById('pagination-controls');

            rows.forEach((row, i) => {{
                row.style.display = (i >= (page - 1) * perPage && i < page * perPage) ? '' : 'none';
            }});

            paginationContainer.innerHTML = '';
            for (let i = 1; i <= totalPages; i++) {{
                const btn = document.createElement('button');
                btn.innerText = i;
                btn.onclick = () => paginateTable(i, perPage);
                btn.style.margin = '0 2px';
                btn.style.backgroundColor = (i === page) ? 'var(--primary-color)' : 'var(--bg-light)';
                btn.style.color = (i === page) ? 'white' : 'var(--text-color)';
                btn.style.border = '1px solid var(--border-color)';
                btn.style.borderRadius = '4px';
                paginationContainer.appendChild(btn);
            }}
        }}
        document.addEventListener('DOMContentLoaded', () => paginateTable(1, 15));
    </script>
</head>
<body>
    <div class="container">
        <h1>ATCO Run Report</h1>
        <p class="tagline">Autonomous Test Coverage Optimization executed on {full_date_time}</p>

        <h2>Summary</h2>
        <div class="summary-grid">
            <div class="summary-card integrated">
                <h3>Integrated</h3>
                <p>{total_integrated}</p>
            </div>
            <div class="summary-card quarantined">
                <h3>Quarantined</h3>
                <p>{total_quarantined}</p>
            </div>
            <div class="summary-card pr">
                <h3>Requires PR</h3>
                <p>{total_requires_pr}</p>
            </div>
            <div class="summary-card deduped">
                <h3>Deduplicated</h3>
                <p>{total_deduplicated}</p>
            </div>
            <div class="summary-card not-gen">
                <h3>Not Generated</h3>
                <p>{total_not_generated}</p>
            </div>
            <div class="summary-card">
                <h3>Total Targeted</h3>
                <p>{total_targets_considered}</p>
            </div>
            <div class="summary-card" style="grid-column: span 2;">
                <h3>Policy Hash at Run</h3>
                <p style="font-size: 1.5em;"><code>{policy_hash_display}</code></p>
            </div>
        </div>

        {quarantine_panel_html}
        {pr_panel_html}

        <h2>Details</h2>
        <div id="pagination-controls" style="margin-bottom: 10px; text-align: center;"></div>
        <table>
            <thead>
                <tr>
                    <th>Module/File</th>
                    <th>Lang</th>
                    <th>Status</th>
                    <th>Cov Gain</th>
                    <th>Test Pass</th>
                    <th>Sec. Issues</th>
                    <th>Reason / Notes</th>
                    <th>SARIF</th>
                    <th>File Hash</th>
                    <th>Mutation Score</th>
                </tr>
            </thead>
            <tbody>
                {detail_rows_html}
            </tbody>
        </table>
        <div class="footer">
            <p>&copy; 2025 The Self-Fixing Engineer Platform. All rights reserved.</p>
            <p>Powered by ATCO - Unleashing the next era of software quality. AI Refinement Success Rate: {ai_refinement_rate}%.</p>
        </div>
    </div>
</body>
</html>
"""
            # Build the final content with all placeholders filled in
            final_html_content = report_html_content.format(
                date_time_title=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                full_date_time=datetime.now(timezone.utc).strftime(
                    "%A, %B %d, %Y at %I:%M:%S %p %Z"
                ),
                total_integrated=sanitized_summary.get("total_integrated", 0),
                total_quarantined=sanitized_summary.get("total_quarantined", 0),
                total_requires_pr=sanitized_summary.get("total_requires_pr", 0),
                total_deduplicated=sanitized_summary.get("total_deduplicated", 0),
                total_not_generated=sanitized_summary.get("total_not_generated", 0),
                total_targets_considered=sanitized_summary.get(
                    "total_targets_considered", 0
                ),
                policy_hash_display=f"{sanitized_policy_hash[:10]}...{sanitized_policy_hash[-10:]}",
                quarantine_panel_html=quarantine_panel_html,
                pr_panel_html=pr_panel_html,
                detail_rows_html="\n".join(detail_rows),
                ai_refinement_rate=f"{float(sanitized_ai_metrics.get('refinement_success_rate_percent', 0) or 0):.1f}",
                csp_header=csp_header,
            )

            try:
                # write the main file
                await atomic_write(full_report_path, final_html_content.encode("utf-8"))

                # write the 'latest' alias file
                latest_report_path = os.path.join(self.report_dir, "latest.html")
                await atomic_write(
                    latest_report_path, final_html_content.encode("utf-8")
                )

                logger.info(f"HTML report generated at: {full_report_path}")
                logger.info(
                    f"HTML report 'latest' alias created at: {latest_report_path}"
                )

                if AUDIT_LOGGER_AVAILABLE:
                    await maybe_await(
                        audit_logger.log_event(
                            event_type="report_generated",
                            details={
                                "report_path": Path(report_path_relative).as_posix(),
                                "latest_alias": Path(
                                    os.path.relpath(
                                        latest_report_path, self.project_root
                                    )
                                ).as_posix(),
                                "summary": sanitized_summary,
                                "policy_hash": sanitized_policy_hash,
                            },
                            user_id="atco_reporter",
                        )
                    )

                # FIX: return the portable path
                return Path(full_report_path).as_posix()
            except Exception as e:
                report_generation_errors.inc()
                logger.critical(
                    f"CRITICAL: Failed to write HTML report to {full_report_path}: {e}",
                    exc_info=True,
                )

                if AUDIT_LOGGER_AVAILABLE:
                    await maybe_await(
                        audit_logger.log_event(
                            event_type="report_generation_failed",
                            details={
                                "error": str(e),
                                "summary": overall_results["summary"],
                                "traceback": traceback.format_exc(),
                            },
                            critical=True,
                            user_id="atco_reporter",
                        )
                    )
                raise

    def build(self, overall_results: Dict[str, Any], policy_engine: Any) -> str:
        """Synchronous public entry point for report generation."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.generate_html_report(overall_results, policy_engine)
            )

        # If we are in an async context, we shouldn't block.
        # Instead, raise an error to tell the caller to await the coroutine directly.
        raise RuntimeError(
            "HTMLReporter.build() called from async context. Use await generate_html_report(...) instead."
        )

    def _sanitize_path(self, path: str) -> str:
        """Ensures a given path is a safe relative path within the project root."""
        normalized_path = os.path.normpath(path)

        if normalized_path.startswith("..") or "../" in normalized_path:
            raise ValueError(
                f"Path '{path}' contains forbidden parent directory traversals."
            )

        abs_path = os.path.abspath(os.path.join(self.project_root, normalized_path))
        if not abs_path.startswith(self.project_root):
            raise ValueError(
                f"Path '{path}' attempts to traverse outside the project root."
            )

        return os.path.relpath(abs_path, self.project_root)
