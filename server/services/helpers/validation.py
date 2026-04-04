"""Validation helpers extracted from ``omnicore_service.py``.

Contains report-structure validation, Helm chart validation, and placeholder
critique-report generation.  These are pure functions with no dependency on
``OmniCoreService`` instance state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Helm Chart Validation Constants
HELM_REQUIRED_FIELDS = {"apiVersion", "name"}


def _create_placeholder_critique_report(job_id: str, message: str) -> Dict[str, Any]:
    """Create a placeholder critique report structure.

    This helper function creates a standardised placeholder report when
    critique is skipped, fails, or is not requested.  This ensures that
    ``reports/critique_report.json`` always exists with a valid structure
    that conforms to the expected report schema.

    Args:
        job_id: The job identifier (should not be empty).
        message: The reason the critique was not performed.

    Returns:
        Dictionary containing the placeholder report structure.

    Raises:
        ValueError: If *job_id* is empty or ``None``.
        TypeError: If *job_id* is not a string.
    """
    if not job_id:
        raise ValueError("job_id cannot be empty or None")

    if not isinstance(job_id, str):
        raise TypeError(f"job_id must be a string, got {type(job_id)}")

    if not message:
        logger.warning(f"Creating placeholder report for job {job_id} with empty message")
        message = "No message provided"

    report: Dict[str, Any] = {
        "job_id": job_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "skipped",
        "message": message,
        "issues_found": 0,
        "issues_fixed": 0,
        "coverage": {
            "total_lines": 0,
            "covered_lines": 0,
            "percentage": 0.0,
        },
        "test_results": {
            "total": 0,
            "passed": 0,
            "failed": 0,
        },
        "issues": [],
        "fixes_applied": [],
        "scan_types": [],
    }

    logger.debug(f"Created placeholder critique report for job {job_id}: {message}")
    return report


def _validate_report_structure(
    report: Any, report_path: Path
) -> Tuple[bool, Optional[str]]:
    """Validate SFE analysis report structure with comprehensive checks.

    Args:
        report: Parsed JSON report data (can be any type).
        report_path: Path to report file (for error messages).

    Returns:
        ``(is_valid, error_message)`` -- *error_message* is ``None`` when valid.
    """
    if not isinstance(report, dict):
        return False, f"Invalid report format: expected dict, got {type(report).__name__}"

    if "all_defects" not in report and "issues" not in report:
        return False, "Report missing required key: 'all_defects' or 'issues'"

    issues = report.get("all_defects", report.get("issues", []))

    if not isinstance(issues, list):
        return False, f"Invalid issues format: expected list, got {type(issues).__name__}"

    if issues:
        non_dict_count = sum(1 for item in issues if not isinstance(item, dict))
        if non_dict_count > 0:
            logger.warning(
                f"Report contains {non_dict_count} non-dict issues",
                extra={
                    "report_path": str(report_path),
                    "total_issues": len(issues),
                    "non_dict_count": non_dict_count,
                },
            )

    return True, None


def _validate_helm_chart_structure(
    chart_data: Dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    """Validate Helm chart data structure against the Helm specification.

    Args:
        chart_data: Parsed Helm chart dictionary.

    Returns:
        ``(is_valid, error_message)``
    """
    if not isinstance(chart_data, dict):
        return False, f"Chart must be a dict, got {type(chart_data).__name__}"

    missing_fields = HELM_REQUIRED_FIELDS - chart_data.keys()
    if missing_fields:
        return False, f"Missing required fields: {missing_fields}"

    api_version = chart_data.get("apiVersion")
    if not isinstance(api_version, str) or not api_version:
        return False, "apiVersion must be a non-empty string"

    name = chart_data.get("name")
    if not isinstance(name, str) or not name:
        return False, "name must be a non-empty string"

    return True, None
