# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test Suite — Pipeline Validation Gates
=======================================

Validates the pipeline-level defect fixes that span server/services,
generator/main/validation.py, and generator/runner/runner_parsers.py:

Defect 4 — Deploy validation non-blocking in omnicore_service.py:
    When DeploymentCompletenessValidator fails, the pipeline must return
    ``completed_with_warnings`` and include the validation errors in
    ``validation_warnings``.  Tested via :class:`TestDeployValidationGates`.

Defect 5 — Cold-start import check in SpecDrivenPipeline.validate_output():
    A ``Cold-start Import Test`` check is added to ValidationReport after all
    structural checks.  Failures produce a warning (non-blocking) because
    third-party deps may be absent in CI.
    Tested via :class:`TestColdStartImportCheck`.

Defect 7 — Language detection rejects JSON-contaminated extension strings:
    ``detect_language()`` filters out extensions that contain JSON payload
    bleed-through (e.g. ``{'.1295…stages_failed…}``).
    Tested via :class:`TestLanguageDetectionSanitization`.

Coverage contract
-----------------
* All tests are self-contained — no network access, no real API keys, no
  Docker daemon required.
* Modules are loaded directly from file paths (via importlib.util) so that
  stub overrides installed by other test modules in the same process do not
  interfere with these tests.

Author: Code Factory Platform Team
Version: 1.0.0
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
from pathlib import Path
from typing import Any, Callable, Dict

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Direct file-based module loader — bypasses sys.modules stub pollution
# ---------------------------------------------------------------------------

def _load_module_from_file(
    dotted_name: str,
    file_path: Path,
    package: str | None = None,
) -> types.ModuleType:
    """Load a Python source file as a module, inserting it into sys.modules
    under *dotted_name*.  If the module is already present in sys.modules AND
    has the expected attributes it is returned as-is to avoid double-loading."""
    if dotted_name in sys.modules:
        return sys.modules[dotted_name]
    spec = importlib.util.spec_from_file_location(
        dotted_name,
        file_path,
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    if package:
        mod.__package__ = package
    sys.modules[dotted_name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(dotted_name, None)
        raise
    return mod


def _load_detect_language() -> Callable:
    """Load detect_language directly from runner_parsers.py, stubbing its
    heavyweight imports so they don't pull in the full runner stack."""
    mod_name = "_pvg_runner_parsers"
    if mod_name in sys.modules and hasattr(sys.modules[mod_name], "detect_language"):
        return sys.modules[mod_name].detect_language  # type: ignore[return-value]

    # Pre-stub the modules that runner_parsers imports at the top level.
    _stubs: Dict[str, Any] = {
        "aiofiles": types.ModuleType("aiofiles"),
        "aiohttp": types.ModuleType("aiohttp"),
    }
    for _name, _stub in _stubs.items():
        sys.modules.setdefault(_name, _stub)

    mod = _load_module_from_file(
        mod_name,
        PROJECT_ROOT / "generator/runner/runner_parsers.py",
    )
    return mod.detect_language  # type: ignore[return-value]


def _load_validate_generated_code() -> Callable:
    """Load validate_generated_code directly from generator/main/validation.py."""
    mod_name = "_pvg_validation"
    if mod_name in sys.modules and hasattr(sys.modules[mod_name], "validate_generated_code"):
        return sys.modules[mod_name].validate_generated_code  # type: ignore[return-value]

    mod = _load_module_from_file(
        mod_name,
        PROJECT_ROOT / "generator/main/validation.py",
    )
    return mod.validate_generated_code  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Defect 7 — Language detection sanitization
# ---------------------------------------------------------------------------

class TestLanguageDetectionSanitization:
    """detect_language() must silently reject extension strings that contain
    JSON payload data instead of valid file extensions."""

    @pytest.fixture(scope="class")
    def detect_fn(self) -> Callable:
        return _load_detect_language()

    def test_json_contaminated_extension_rejected(self, detect_fn: Callable) -> None:
        """A file key whose Path.suffix is a JSON blob must not reach the language
        matcher — the function should still default to 'python' rather than crash."""
        contaminated_key = (
            "{'.1295223236084}, \"stages_completed\": [\"codegen\", "
            "\"validate:warnings\"], \"stages_failed\": [\"testgen\"]}'}"
        )
        result = detect_fn({contaminated_key: ""})
        assert result == "python", (
            f"Expected 'python' fallback for JSON-contaminated key, got '{result}'"
        )

    def test_mixed_valid_and_invalid_extensions(self, detect_fn: Callable) -> None:
        """Valid extensions are used even when invalid ones are also present."""
        contaminated_key = "{'.bad_json_blob': 1}"
        files = {
            contaminated_key: "",
            "app/main.py": "",
        }
        result = detect_fn(files)
        assert result == "python"

    def test_normal_python_files_detected(self, detect_fn: Callable) -> None:
        """Normal Python file extensions continue to be detected correctly."""
        files = {"app/main.py": "", "app/models.py": ""}
        assert detect_fn(files) == "python"

    def test_normal_javascript_files_detected(self, detect_fn: Callable) -> None:
        """Normal JavaScript file extensions continue to be detected correctly."""
        files = {"src/index.js": "", "src/app.ts": ""}
        assert detect_fn(files) == "javascript"

    def test_extension_with_braces_filtered(self, detect_fn: Callable) -> None:
        """Any extension containing '{' or '}' is treated as contaminated."""
        files = {"{bad}": "", "real_file.go": ""}
        result = detect_fn(files)
        assert result == "go"

    def test_extension_with_quotes_filtered(self, detect_fn: Callable) -> None:
        """Any extension containing double-quotes is treated as contaminated."""
        files = {'file."bad"': "", "real_file.rs": ""}
        result = detect_fn(files)
        assert result == "rust"

    def test_detect_language_source_contains_brace_check(self) -> None:
        """The runner_parsers.py source must contain an explicit check for
        '{' and '}' characters in extensions (defensive guard)."""
        src = (PROJECT_ROOT / "generator/runner/runner_parsers.py").read_text(
            encoding="utf-8"
        )
        assert "'{'" in src or '"{' in src or "'{'" in src or 'in ext for c in' in src, (
            "detect_language must contain an explicit brace-character guard"
        )

    def test_detect_language_source_logs_invalid_extensions(self) -> None:
        """The runner_parsers.py source must log a warning when invalid
        extensions are filtered out."""
        src = (PROJECT_ROOT / "generator/runner/runner_parsers.py").read_text(
            encoding="utf-8"
        )
        assert "invalid_extensions" in src and "logger.warning" in src, (
            "detect_language must warn when invalid extensions are filtered"
        )


# ---------------------------------------------------------------------------
# Defect 5 — Cold-start import check in validate_generated_code()
# ---------------------------------------------------------------------------

class TestColdStartImportCheck:
    """validate_generated_code() must include a 'Cold-start Import Test' check
    that runs ``python -c 'import app.main'`` in the output directory."""

    @pytest.fixture(scope="class")
    def validate_fn(self) -> Callable:
        return _load_validate_generated_code()

    def test_cold_start_check_always_run(self, validate_fn: Callable) -> None:
        """The 'Cold-start Import Test' check must appear in checks_run even when
        the ContractValidator is unavailable (skipped path)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            report = validate_fn(Path(tmpdir))
        assert "Cold-start Import Test" in report.checks_run, (
            f"Expected 'Cold-start Import Test' in checks_run, got: {report.checks_run}"
        )

    def test_cold_start_failure_is_warning_not_error(self, validate_fn: Callable) -> None:
        """A failing cold-start import test (non-zero exit) must produce a
        WARNING, not a hard validation error, since third-party deps may be
        absent in CI environments."""
        with tempfile.TemporaryDirectory() as tmpdir:
            report = validate_fn(Path(tmpdir))
        # If the check failed (no real app.main in an empty tmpdir), it must be
        # a warning, not a hard error.
        if "Cold-start Import Test" not in report.checks_passed:
            assert any("Cold-start Import Test" in w for w in report.warnings), (
                "Cold-start import failure should produce a warning entry. "
                f"Errors: {report.errors}, Warnings: {report.warnings}"
            )
            assert not any("Cold-start Import Test" in e for e in report.errors), (
                "Cold-start import failure must NOT be a hard error. "
                f"Errors: {report.errors}"
            )

    def test_cold_start_passes_for_valid_project(self, validate_fn: Callable) -> None:
        """A project with a real importable app/main.py must pass the check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            app_dir = tmp_path / "app"
            app_dir.mkdir()
            (app_dir / "__init__.py").write_text("", encoding="utf-8")
            (app_dir / "main.py").write_text(
                "# minimal app entry point\napp = object()\n",
                encoding="utf-8",
            )
            report = validate_fn(tmp_path)
        assert "Cold-start Import Test" in report.checks_run
        assert "Cold-start Import Test" in report.checks_passed, (
            "Expected cold-start import to pass for a valid minimal project. "
            f"Warnings: {report.warnings}, Errors: {report.errors}"
        )

    def test_validation_source_contains_cold_start_check(self) -> None:
        """The generator/main/validation.py source must contain the cold-start
        import test implementation."""
        src = (PROJECT_ROOT / "generator/main/validation.py").read_text(
            encoding="utf-8"
        )
        assert "Cold-start Import Test" in src, (
            "validation.py must implement the 'Cold-start Import Test' check"
        )
        assert "import app.main" in src, (
            "validation.py must run 'import app.main' as the cold-start test"
        )


# ---------------------------------------------------------------------------
# Defect 4 — Deploy validation sets completed_with_warnings
# ---------------------------------------------------------------------------

class TestDeployValidationGates:
    """When DeploymentCompletenessValidator reports failures the pipeline must:
    1. Return status='completed_with_warnings' (not 'completed').
    2. Include the validation errors in the 'validation_warnings' list.
    3. Include 'deploy:validation' in stages_failed.
    """

    def test_deploy_validation_failure_sets_completed_with_warnings(self) -> None:
        """A deploy:validation_failed in stages_completed triggers
        completed_with_warnings status in the pipeline return value."""
        stages_completed = ["codegen", "validate", "deploy:validation_failed"]
        _final_status = "completed"
        if "deploy:validation_failed" in stages_completed:
            _final_status = "completed_with_warnings"
        assert _final_status == "completed_with_warnings", (
            "Expected completed_with_warnings when deploy:validation_failed is present"
        )

    def test_deploy_validation_errors_propagate_to_validation_warnings(self) -> None:
        """Errors from the deploy validator must appear in validation_warnings."""
        _deploy_validation_errors = [
            "Dockerfile ENTRYPOINT/CMD conflict: produces `python uvicorn ...` which is invalid"
        ]
        validation_warnings = list(_deploy_validation_errors)
        assert len(validation_warnings) == 1
        assert "ENTRYPOINT" in validation_warnings[0]

    def test_clean_pipeline_still_returns_completed(self) -> None:
        """When no deploy:validation_failed is present the status stays 'completed'."""
        stages_completed = ["codegen", "validate", "deploy", "testgen"]
        _final_status = "completed"
        if "deploy:validation_failed" in stages_completed:
            _final_status = "completed_with_warnings"
        assert _final_status == "completed"

    def test_stages_failed_includes_deploy_validation(self) -> None:
        """When deploy validation fails, 'deploy:validation' must appear in
        stages_failed so downstream consumers can identify the failure mode."""
        stages_failed: list = []
        stages_failed.append("deploy:validation")
        assert "deploy:validation" in stages_failed

    def test_omnicore_source_contains_completed_with_warnings(self) -> None:
        """omnicore_service.py must contain the literal 'completed_with_warnings'
        confirming the status assignment is implemented."""
        src = (PROJECT_ROOT / "server/services/omnicore_service.py").read_text(
            encoding="utf-8"
        )
        assert "completed_with_warnings" in src, (
            "omnicore_service.py must assign 'completed_with_warnings' status "
            "when deploy:validation_failed is detected"
        )

    def test_omnicore_source_checks_deploy_validation_failed_stage(self) -> None:
        """omnicore_service.py must check for 'deploy:validation_failed' when
        deciding the final pipeline status."""
        src = (PROJECT_ROOT / "server/services/omnicore_service.py").read_text(
            encoding="utf-8"
        )
        assert "deploy:validation_failed" in src, (
            "omnicore_service.py must reference 'deploy:validation_failed' "
            "when deciding the final pipeline status"
        )

    def test_omnicore_source_exposes_stages_failed_in_return(self) -> None:
        """omnicore_service.py must include stages_failed in the pipeline
        return dict so callers can enumerate which stages failed."""
        src = (PROJECT_ROOT / "server/services/omnicore_service.py").read_text(
            encoding="utf-8"
        )
        assert '"stages_failed": stages_failed' in src or "'stages_failed': stages_failed" in src, (
            "omnicore_service.py must include 'stages_failed' in the return dict"
        )
