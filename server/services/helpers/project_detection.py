"""Project detection helpers extracted from ``omnicore_service.py``.

Functions for detecting project language, identifying test files,
extracting project names from payloads, and classifying import errors.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Language detection and file extension mappings
LANGUAGE_FILE_EXTENSIONS: Dict[str, list] = {
    "python": ["*.py"],
    "typescript": ["*.ts", "*.tsx"],
    "javascript": ["*.js", "*.jsx"],
    "java": ["*.java"],
    "go": ["*.go"],
    "rust": ["*.rs"],
}

TEST_FILE_PATTERNS = {
    "python": lambda f: f.name.startswith("test_") or f.name.endswith("_test.py"),
    "typescript": lambda f: (
        f.name.endswith(".test.ts") or f.name.endswith(".spec.ts")
        or f.name.endswith(".test.tsx") or f.name.endswith(".spec.tsx")
    ),
    "javascript": lambda f: (
        f.name.endswith(".test.js") or f.name.endswith(".spec.js")
        or f.name.endswith(".test.jsx") or f.name.endswith(".spec.jsx")
    ),
    "java": lambda f: f.name.endswith("Test.java") or f.name.endswith("Tests.java"),
    "go": lambda f: f.name.endswith("_test.go"),
    "rust": lambda f: f.name.startswith("test_") or "tests" in str(f.parent),
}


def _detect_project_language(code_path: Path) -> str:
    """Detect the primary programming language of a project by counting files.

    Args:
        code_path: Path to the project directory.

    Returns:
        Language name (e.g. ``"python"``).  Defaults to ``"python"``.
    """
    if not code_path.exists():
        logger.warning(f"Code path {code_path} does not exist, defaulting to python")
        return "python"

    file_counts: Dict[str, int] = {}
    for language, patterns in LANGUAGE_FILE_EXTENSIONS.items():
        count = 0
        for pattern in patterns:
            count += len(list(code_path.rglob(pattern)))
        file_counts[language] = count

    if not file_counts or max(file_counts.values()) == 0:
        logger.info(f"No recognized source files found in {code_path}, defaulting to python")
        return "python"

    detected_language = max(file_counts, key=file_counts.get)
    logger.info(
        f"Detected project language: {detected_language} "
        f"(file counts: {file_counts})"
    )
    return detected_language


def _is_test_file(file_path: Path, language: str) -> bool:
    """Check if a file is a test file based on language-specific patterns.

    Args:
        file_path: Path to the file.
        language: Programming language.

    Returns:
        ``True`` if the file is a test file.
    """
    pattern_func = TEST_FILE_PATTERNS.get(language)
    if pattern_func:
        return pattern_func(file_path)
    return file_path.name.startswith("test_") or "test" in file_path.name.lower()


def _extract_project_name_from_path_or_payload(
    payload: Dict[str, Any],
    default: Optional[str] = None,
) -> Optional[str]:
    """Extract project name from *payload* in a consistent way.

    Priority order:
    1. ``package_name`` or ``package`` field from payload.
    2. Last component of ``output_dir`` path.
    3. Caller-supplied *default* (``None`` by default).

    Args:
        payload: Job payload containing ``output_dir``, ``package_name``, etc.
        default: Fallback value when no name can be determined.

    Returns:
        Project name string or *default*.
    """
    project_name = payload.get("package_name") or payload.get("package")

    if not project_name:
        output_dir = payload.get("output_dir", "").strip()
        if output_dir:
            path_parts = output_dir.replace("\\", "/").strip("/").split("/")
            path_parts = [p for p in path_parts if p]
            if path_parts:
                project_name = path_parts[-1]

    if not project_name:
        project_name = default

    return project_name


def _is_third_party_import_error(error_str: str) -> bool:
    """Return ``True`` if *error_str* describes a missing third-party package.

    Args:
        error_str: A validation error message string.

    Returns:
        ``True`` if the error is caused by a missing third-party package.
    """
    match = re.search(r"No module named '([^']+)'", error_str)
    if not match:
        return False
    module_top = match.group(1).split(".")[0]
    _LOCAL_PREFIXES = {"app", "tests", "test", "server", "generator", "self_fixing_engineer"}
    return module_top not in _LOCAL_PREFIXES
