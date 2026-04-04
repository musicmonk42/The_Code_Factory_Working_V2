"""Helpers package -- re-exports all public helper functions.

Consumers can import directly from ``server.services.helpers`` instead of
reaching into sub-modules::

    from server.services.helpers import _detect_project_language, _fix_double_nesting
"""

from __future__ import annotations

from server.services.helpers.validation import (
    _create_placeholder_critique_report,
    _validate_helm_chart_structure,
    _validate_report_structure,
)
from server.services.helpers.project_detection import (
    _detect_project_language,
    _extract_project_name_from_path_or_payload,
    _is_test_file,
    _is_third_party_import_error,
)
from server.services.helpers.fallback_generators import (
    _generate_fallback_frontend_files,
    _generate_fallback_readme,
    _load_readme_from_disk,
)
from server.services.helpers.file_utils import (
    _build_delta_prompt,
    _ensure_python_package_structure,
    _fix_double_nesting,
    _pre_materialization_import_check,
)
from server.services.helpers.sfe_cache import (
    _invalidate_sfe_analysis_cache,
    _load_sfe_analysis_report,
)

__all__ = [
    "_build_delta_prompt",
    "_create_placeholder_critique_report",
    "_detect_project_language",
    "_ensure_python_package_structure",
    "_extract_project_name_from_path_or_payload",
    "_fix_double_nesting",
    "_generate_fallback_frontend_files",
    "_generate_fallback_readme",
    "_invalidate_sfe_analysis_cache",
    "_is_test_file",
    "_is_third_party_import_error",
    "_load_readme_from_disk",
    "_load_sfe_analysis_report",
    "_pre_materialization_import_check",
    "_validate_helm_chart_structure",
    "_validate_report_structure",
]
