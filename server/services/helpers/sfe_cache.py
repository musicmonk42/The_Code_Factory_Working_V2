"""SFE analysis report cache helpers extracted from ``omnicore_service.py``.

Functions for loading, validating, and invalidating the cached SFE
(Self-Fixing Engineer) analysis report on disk.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from server.services.helpers.validation import _validate_report_structure

logger = logging.getLogger(__name__)

# File Size Limits
MAX_REPORT_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Cache/Report Source Constants
SOURCE_CACHE = "sfe_analysis_report"


# NOTE: _load_sfe_analysis_report exceeds the 40-line guideline (~60 lines).
# Preserved as-is for behavioural equivalence.
def _load_sfe_analysis_report(
    report_path: Path,
    job_id: str,
    max_file_size: int = MAX_REPORT_FILE_SIZE_BYTES,
) -> Optional[Dict[str, Any]]:
    """Load and validate SFE analysis report with comprehensive error handling.

    Args:
        report_path: Path to ``sfe_analysis_report.json``.
        job_id: Job identifier for logging context.
        max_file_size: Maximum allowed file size in bytes.

    Returns:
        Dictionary with keys ``issues``, ``count``, ``source``, ``cached``,
        or ``None`` if report is missing/invalid.
    """
    if not report_path.exists() or not report_path.is_file():
        return None

    try:
        file_size = report_path.stat().st_size
        if file_size > max_file_size:
            logger.warning(
                f"[SFE] Analysis report file too large ({file_size} bytes), skipping cache",
                extra={"job_id": job_id, "file_size": file_size, "max_size": max_file_size,
                       "report_path": str(report_path)},
            )
            return None

        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)

        is_valid, error_msg = _validate_report_structure(report, report_path)
        if not is_valid:
            raise ValueError(error_msg)

        issues = report.get("all_defects", report.get("issues", []))

        logger.info(
            f"[SFE] Loaded {len(issues)} issues from cached analysis report",
            extra={
                "job_id": job_id,
                "issue_count": len(issues),
                "report_age_seconds": (
                    datetime.now(timezone.utc).timestamp() - report_path.stat().st_mtime
                ),
                "source": "cache",
            },
        )

        return {
            "issues": issues,
            "count": len(issues),
            "source": SOURCE_CACHE,
            "cached": True,
        }

    except json.JSONDecodeError as e:
        logger.warning(
            f"[SFE] Invalid JSON in analysis report: {e}",
            extra={"job_id": job_id, "report_path": str(report_path), "error": str(e)},
        )
    except (IOError, OSError) as e:
        logger.warning(
            f"[SFE] Failed to read analysis report: {type(e).__name__}: {e}",
            extra={"job_id": job_id, "report_path": str(report_path),
                   "error_type": type(e).__name__},
        )
    except ValueError as e:
        logger.warning(
            f"[SFE] Invalid report structure: {e}",
            extra={"job_id": job_id, "report_path": str(report_path), "error": str(e)},
        )
    except Exception as e:
        logger.warning(
            f"[SFE] Unexpected error loading report: {type(e).__name__}: {e}",
            extra={"job_id": job_id, "report_path": str(report_path),
                   "error_type": type(e).__name__},
            exc_info=True,
        )

    return None


def _invalidate_sfe_analysis_cache(job_path: Path, job_id: str) -> None:
    """Delete the cached SFE analysis report so the next run re-analyses."""
    report_path = job_path / "reports" / "sfe_analysis_report.json"
    if report_path.exists():
        try:
            os.remove(report_path)
            logger.info(
                f"[SFE] Invalidated cached analysis report for job {job_id}",
                extra={"job_id": job_id, "report_path": str(report_path)},
            )
        except OSError as e:
            logger.warning(
                f"[SFE] Could not delete cached analysis report for job {job_id}: {e}",
                extra={"job_id": job_id},
            )
