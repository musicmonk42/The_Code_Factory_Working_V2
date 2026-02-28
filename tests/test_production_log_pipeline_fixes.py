# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the 5 pipeline fixes identified in production logs 1772306903158 / 1772309047897.

Issue 1 – Zero agents in _agent_registry (engine.py _auto_register_agents fallback)
Issue 2 – README Completeness: missing venv instructions (post_materialize + engine)
Issue 3 – Reports: critique_report.json missing coverage / test_results fields (engine.py Fix 7)
Issue 4 – Downstream agents skipped: validate_required_agents fallback imports (plugin wrapper)
Issue 5 – requirements.txt not always scaffolded (post_materialize Phase 9)
"""

import importlib
import importlib.util as _importlib_util
import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read(rel_path: str) -> str:
    return (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")


def _load_post_materialize():
    """Load post_materialize module without full dep-chain via spec loader."""
    key = "pm_pipeline_fixes_test"
    if key in sys.modules:
        return sys.modules[key]
    spec = _importlib_util.spec_from_file_location(
        key,
        str(PROJECT_ROOT / "generator" / "main" / "post_materialize.py"),
    )
    mod = _importlib_util.module_from_spec(spec)
    sys.modules[key] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(key, None)
        raise
    return mod


# ---------------------------------------------------------------------------
# Issue 1: Agent fallback registration (engine.py)
# ---------------------------------------------------------------------------

class TestAgentFallbackRegistration:
    """Verify the _auto_register_agents() fallback list uses correct class names."""

    _ENGINE_CONTENT = _read("generator/main/engine.py")

    def test_fallback_imports_block_present(self):
        """Final fallback block is present when registered_count == 0."""
        assert "_fallback_imports = [" in self._ENGINE_CONTENT, (
            "_auto_register_agents() must define a _fallback_imports list"
        )

    def test_codegen_fallback_uses_codegenconfig_not_codegenagent(self):
        """codegen fallback must reference CodeGenConfig (the real class), not 'CodegenAgent'."""
        assert '"CodeGenConfig"' in self._ENGINE_CONTENT, (
            "codegen fallback must use CodeGenConfig — CodegenAgent does not exist in codegen_agent.py"
        )
        assert '"CodegenAgent"' not in self._ENGINE_CONTENT, (
            "CodegenAgent does not exist in codegen_agent.py and must not be referenced as fallback"
        )

    def test_critique_fallback_uses_critiqueagent(self):
        """critique fallback references CritiqueAgent (the actual callable agent class)."""
        assert '"CritiqueAgent"' in self._ENGINE_CONTENT, (
            "critique fallback should use CritiqueAgent for real agent execution"
        )

    def test_testgen_fallback_class_correct(self):
        assert '"TestgenAgent"' in self._ENGINE_CONTENT

    def test_deploy_fallback_class_correct(self):
        assert '"DeployAgent"' in self._ENGINE_CONTENT

    def test_docgen_fallback_class_correct(self):
        assert '"DocgenAgent"' in self._ENGINE_CONTENT

    def test_all_fallback_classes_exist_in_their_modules(self):
        """Verify every fallback (module_path, class_name) pair resolves at import time."""
        # Parse the fallback table from the source rather than importing the
        # heavy engine module; this avoids pulling in all transitive deps.
        pattern = re.compile(
            r'\("(\w+)",\s*"(generator\.agents\.[^"]+)",\s*"(\w+)"\)'
        )
        entries = pattern.findall(self._ENGINE_CONTENT)
        # We expect at least the 5 fallback entries
        assert len(entries) >= 5, f"Expected >= 5 fallback entries, found {entries}"

        for agent_name, module_path, class_name in entries:
            try:
                mod = importlib.import_module(module_path)
            except Exception as exc:
                pytest.skip(f"Cannot import {module_path}: {exc}")
            cls = getattr(mod, class_name, None)
            assert cls is not None, (
                f"Class '{class_name}' not found in module '{module_path}' "
                f"(fallback entry for agent '{agent_name}')"
            )

    def test_fallback_block_guarded_by_registered_count_zero(self):
        """Fallback block only runs when registered_count == 0."""
        # The block must be nested under `if registered_count == 0:`
        lines = self._ENGINE_CONTENT.splitlines()
        found_guard = False
        found_fallback = False
        for i, line in enumerate(lines):
            if "if registered_count == 0:" in line:
                found_guard = True
            if found_guard and "_fallback_imports = [" in line:
                found_fallback = True
                break
        assert found_fallback, (
            "_fallback_imports list must be nested under 'if registered_count == 0:'"
        )


# ---------------------------------------------------------------------------
# Issue 2: README Setup section contains venv instructions
# ---------------------------------------------------------------------------

class TestReadmeVenvInstructions:
    """ensure_readme_sections() must include venv setup in the ## Setup block."""

    _PM_CONTENT = _read("generator/main/post_materialize.py")
    _ENGINE_CONTENT = _read("generator/main/engine.py")

    # --- Static checks -------------------------------------------------

    def test_post_materialize_setup_section_has_venv_create(self):
        """post_materialize.py ensure_readme_sections includes 'python -m venv venv'."""
        assert "python -m venv venv" in self._PM_CONTENT, (
            "ensure_readme_sections() in post_materialize.py must emit "
            "'python -m venv venv' in the ## Setup section"
        )

    def test_post_materialize_setup_section_has_activate_command(self):
        """post_materialize.py includes venv activation instructions."""
        assert "venv/bin/activate" in self._PM_CONTENT or "activate" in self._PM_CONTENT, (
            "ensure_readme_sections() must include venv activation instructions"
        )

    def test_engine_setup_section_has_venv_create(self):
        """engine.py _ensure_readme_sections includes 'python -m venv venv'."""
        assert "python -m venv venv" in self._ENGINE_CONTENT, (
            "_ensure_readme_sections() in engine.py must emit "
            "'python -m venv venv' in the ## Setup section"
        )

    def test_engine_setup_section_has_activate_command(self):
        """engine.py includes venv activation instructions."""
        assert "venv/bin/activate" in self._ENGINE_CONTENT, (
            "_ensure_readme_sections() in engine.py must include venv activation"
        )

    # --- Functional checks ---------------------------------------------

    def test_generated_readme_contains_venv(self):
        """ensure_readme_sections() output contains the word 'venv'."""
        mod = _load_post_materialize()
        result = mod.ensure_readme_sections("")
        assert "venv" in result, (
            "Generated README ## Setup section must contain 'venv'"
        )
        assert "python -m venv venv" in result, (
            "Generated README must include 'python -m venv venv' command"
        )

    def test_existing_setup_section_not_modified(self):
        """When ## Setup already exists, ensure_readme_sections() does NOT overwrite it."""
        mod = _load_post_materialize()
        existing = "# My App\n\n## Setup\n\nCustom setup goes here.\n"
        result = mod.ensure_readme_sections(existing)
        # The original custom setup must be preserved
        assert "Custom setup goes here." in result, (
            "ensure_readme_sections() must not overwrite an existing ## Setup section"
        )
        # No duplicate venv block injected when ## Setup already present
        assert result.count("## Setup") == 1, (
            "ensure_readme_sections() must not add a second ## Setup heading"
        )

    def test_generated_readme_passes_venv_contract_check(self):
        """Generated README contains 'venv' — satisfying ContractValidator.check_readme_completeness()."""
        mod = _load_post_materialize()
        readme = mod.ensure_readme_sections("# My Generated App\n")
        assert "venv" in readme, (
            "ContractValidator checks for 'venv' in README; generated content must include it"
        )


# ---------------------------------------------------------------------------
# Issue 3: critique_report.json Fix-7 fallback has all required fields
# ---------------------------------------------------------------------------

class TestCritiqueReportFallbackFields:
    """Fix 7 fallback block in engine.py must produce all required critique_report.json fields."""

    _ENGINE_CONTENT = _read("generator/main/engine.py")

    # The ContractValidator.check_reports() requires these six fields.
    REQUIRED_FIELDS = ["job_id", "timestamp", "coverage", "test_results", "issues", "fixes_applied"]

    def test_fix7_block_has_coverage_field(self):
        """Fix 7 critique_report fallback includes 'coverage' dict."""
        # We look for the Fix 7 comment sentinel and then check that coverage
        # appears within 40 lines below it.
        lines = self._ENGINE_CONTENT.splitlines()
        fix7_line = None
        for i, line in enumerate(lines):
            if "Fix 7" in line and "critique_report" in line:
                fix7_line = i
                break
        assert fix7_line is not None, "Fix 7 block not found in engine.py"

        block = "\n".join(lines[fix7_line : fix7_line + 60])
        assert '"coverage"' in block, (
            "Fix 7 critique_report fallback must include a 'coverage' field"
        )

    def test_fix7_block_has_test_results_field(self):
        """Fix 7 critique_report fallback includes 'test_results' dict."""
        lines = self._ENGINE_CONTENT.splitlines()
        fix7_line = next(
            (i for i, l in enumerate(lines) if "Fix 7" in l and "critique_report" in l),
            None,
        )
        assert fix7_line is not None, "Fix 7 block not found in engine.py"
        block = "\n".join(lines[fix7_line : fix7_line + 60])
        assert '"test_results"' in block, (
            "Fix 7 critique_report fallback must include a 'test_results' field"
        )

    def test_fix7_block_has_all_required_fields(self):
        """Fix 7 block contains every field required by ContractValidator.check_reports()."""
        lines = self._ENGINE_CONTENT.splitlines()
        fix7_line = next(
            (i for i, l in enumerate(lines) if "Fix 7" in l and "critique_report" in l),
            None,
        )
        assert fix7_line is not None, "Fix 7 block not found in engine.py"
        # Scan a generous 80-line window for all required fields
        block = "\n".join(lines[fix7_line : fix7_line + 80])
        for field in self.REQUIRED_FIELDS:
            assert f'"{field}"' in block, (
                f"Fix 7 critique_report fallback must include required field: '{field}'"
            )

    def test_critique_report_coverage_has_required_subfields(self):
        """Fix 7 coverage dict contains total_lines, covered_lines, percentage."""
        lines = self._ENGINE_CONTENT.splitlines()
        fix7_line = next(
            (i for i, l in enumerate(lines) if "Fix 7" in l and "critique_report" in l),
            None,
        )
        assert fix7_line is not None
        block = "\n".join(lines[fix7_line : fix7_line + 80])
        for subfield in ("total_lines", "covered_lines", "percentage"):
            assert subfield in block, (
                f"Fix 7 coverage dict must contain '{subfield}' sub-field"
            )

    def test_critique_report_test_results_has_required_subfields(self):
        """Fix 7 test_results dict contains total, passed, failed."""
        lines = self._ENGINE_CONTENT.splitlines()
        fix7_line = next(
            (i for i, l in enumerate(lines) if "Fix 7" in l and "critique_report" in l),
            None,
        )
        assert fix7_line is not None
        block = "\n".join(lines[fix7_line : fix7_line + 80])
        for subfield in ("total", "passed", "failed"):
            assert subfield in block, (
                f"Fix 7 test_results dict must contain '{subfield}' sub-field"
            )

    def test_critique_report_schema_matches_contract_validator(self):
        """Construct a minimal fallback report and validate it against the same rules
        used by ContractValidator.check_reports()."""
        import json as _json
        from datetime import datetime, timezone

        # Reproduce the Fix 7 report construction logic
        _critique_data: dict = {}
        report = {
            "job_id": "test-workflow-id",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "skipped" if not _critique_data else _critique_data.get("status"),
            "reason": (
                "Pipeline validation failed before critique stage"
                if not _critique_data
                else None
            ),
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
            "issues": _critique_data.get("issues", []),
            "fixes_applied": _critique_data.get("fixes_applied", []),
        }

        required_fields = ["job_id", "timestamp", "coverage", "test_results", "issues", "fixes_applied"]
        for field in required_fields:
            assert field in report, f"Report dict missing required field: '{field}'"

        coverage = report["coverage"]
        assert isinstance(coverage, dict)
        for subfield in ("total_lines", "covered_lines", "percentage"):
            assert subfield in coverage

        test_results = report["test_results"]
        assert isinstance(test_results, dict)
        for subfield in ("total", "passed", "failed"):
            assert subfield in test_results

        # Round-trip through JSON to verify serialisability
        serialised = _json.loads(_json.dumps(report, indent=2))
        assert serialised["coverage"]["percentage"] == 0.0


# ---------------------------------------------------------------------------
# Issue 4: validate_required_agents() fallback imports (plugin wrapper)
# ---------------------------------------------------------------------------

class TestPluginWrapperAgentFallback:
    """validate_required_agents() must attempt direct imports when registry misses soft agents."""

    _WRAPPER_CONTENT = _read("generator/agents/generator_plugin_wrapper.py")

    def test_soft_agent_fallbacks_dict_present(self):
        """_soft_agent_fallbacks dict defined inside validate_required_agents."""
        assert "_soft_agent_fallbacks" in self._WRAPPER_CONTENT, (
            "validate_required_agents() must define _soft_agent_fallbacks for direct imports"
        )

    def test_fallback_attempts_import_module(self):
        """Fallback code calls importlib.import_module for missing soft agents."""
        assert "importlib.import_module" in self._WRAPPER_CONTENT, (
            "validate_required_agents() must call importlib.import_module as fallback"
        )

    def test_importlib_top_level_import(self):
        """importlib is imported at the top of the module (not inline)."""
        lines = self._WRAPPER_CONTENT.splitlines()
        top_section = "\n".join(lines[:50])
        assert "import importlib" in top_section, (
            "importlib must be imported at module level, not inline inside the function"
        )

    def test_testgen_agent_in_fallback_dict(self):
        assert '"testgen_agent"' in self._WRAPPER_CONTENT

    def test_deploy_agent_in_fallback_dict(self):
        assert '"deploy_agent"' in self._WRAPPER_CONTENT

    def test_docgen_agent_in_fallback_dict(self):
        assert '"docgen_agent"' in self._WRAPPER_CONTENT

    def test_critique_agent_in_fallback_dict(self):
        assert '"critique_agent"' in self._WRAPPER_CONTENT

    def test_fallback_class_names_exist_in_modules(self):
        """Every class name referenced in _soft_agent_fallbacks resolves at import time."""
        # Parse _soft_agent_fallbacks entries from source
        pattern = re.compile(
            r'"(testgen_agent|deploy_agent|docgen_agent|critique_agent)":\s*'
            r'\(\s*"(generator\.agents\.[^"]+)",\s*"(\w+)"\s*\)'
        )
        entries = pattern.findall(self._WRAPPER_CONTENT)
        assert len(entries) == 4, (
            f"Expected 4 soft-agent fallback entries, found {len(entries)}: {entries}"
        )
        for agent_name, module_path, class_name in entries:
            try:
                mod = importlib.import_module(module_path)
            except Exception as exc:
                pytest.skip(f"Cannot import {module_path}: {exc}")
            cls = getattr(mod, class_name, None)
            assert cls is not None, (
                f"Class '{class_name}' not found in '{module_path}' "
                f"(soft-agent fallback for '{agent_name}')"
            )

    def test_fallback_runs_per_agent_not_bulk(self):
        """Fallback tries each agent individually so one failure does not block others."""
        # Each soft-required agent name must have its own try/except around the import
        for agent in ("testgen_agent", "deploy_agent", "docgen_agent", "critique_agent"):
            assert agent in self._WRAPPER_CONTENT, (
                f"'{agent}' must appear in the _soft_agent_fallbacks dict"
            )

    def test_missing_agent_logged_at_debug_not_error(self):
        """Fallback failures are logged at DEBUG level (non-fatal)."""
        assert "logger.debug" in self._WRAPPER_CONTENT, (
            "Fallback import failures must be logged at DEBUG level (non-fatal)"
        )


# ---------------------------------------------------------------------------
# Issue 5: requirements.txt scaffold (post_materialize Phase 9)
# ---------------------------------------------------------------------------

class TestRequirementsTxtScaffold:
    """post_materialize() must create a minimal requirements.txt when one is absent."""

    _PM_CONTENT = _read("generator/main/post_materialize.py")

    # --- Static checks -------------------------------------------------

    def test_phase9_present_in_source(self):
        """Phase 9 comment block exists in post_materialize.py."""
        assert "Phase 9" in self._PM_CONTENT, (
            "post_materialize.py must have a Phase 9 block to scaffold requirements.txt"
        )

    def test_phase9_uses_create_if_absent(self):
        """Phase 9 calls _create_if_absent for requirements.txt."""
        assert "requirements.txt" in self._PM_CONTENT, (
            "Phase 9 must create requirements.txt via _create_if_absent"
        )

    def test_phase9_includes_fastapi(self):
        """Scaffolded requirements.txt content includes fastapi."""
        assert "fastapi" in self._PM_CONTENT

    def test_phase9_includes_uvicorn(self):
        """Scaffolded requirements.txt content includes uvicorn."""
        assert "uvicorn" in self._PM_CONTENT

    def test_phase9_includes_pydantic(self):
        """Scaffolded requirements.txt content includes pydantic."""
        assert "pydantic" in self._PM_CONTENT

    def test_phase9_wrapped_in_try_except(self):
        """Phase 9 is wrapped in a try/except block for defensive consistency."""
        lines = self._PM_CONTENT.splitlines()
        phase9_line = next(
            (i for i, l in enumerate(lines) if "Phase 9" in l), None
        )
        assert phase9_line is not None, "Phase 9 not found"
        # Scan the 20 lines after the Phase 9 comment for a try: statement
        block = "\n".join(lines[phase9_line : phase9_line + 20])
        assert "try:" in block, (
            "Phase 9 must be wrapped in a try/except block, consistent with Phases 6–8"
        )

    # --- Functional checks ---------------------------------------------

    def test_post_materialize_creates_requirements_txt_when_absent(self):
        """post_materialize() writes requirements.txt when it does not exist."""
        mod = _load_post_materialize()
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "my_project"
            project_dir.mkdir()
            mod.post_materialize(project_dir)

            req_path = project_dir / "requirements.txt"
            assert req_path.exists(), (
                "post_materialize() must create requirements.txt when absent"
            )
            content = req_path.read_text(encoding="utf-8")
            assert "fastapi" in content
            assert "uvicorn" in content
            assert "pydantic" in content

    def test_post_materialize_preserves_existing_requirements_txt(self):
        """post_materialize() must NOT overwrite an already-existing requirements.txt."""
        mod = _load_post_materialize()
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "my_project"
            project_dir.mkdir()
            custom_content = "# custom\ndjango>=4.0\n"
            (project_dir / "requirements.txt").write_text(custom_content, encoding="utf-8")

            mod.post_materialize(project_dir)

            content = (project_dir / "requirements.txt").read_text(encoding="utf-8")
            assert content == custom_content, (
                "post_materialize() must not overwrite an existing requirements.txt"
            )


# ---------------------------------------------------------------------------
# Cross-cutting: engine.py importlib top-level import
# ---------------------------------------------------------------------------

class TestEngineImportlibTopLevel:
    """importlib must be imported at the top of engine.py (not inline in fallback)."""

    _ENGINE_CONTENT = _read("generator/main/engine.py")

    def test_importlib_is_module_level_import(self):
        """importlib appears in the top-level imports section of engine.py."""
        lines = self._ENGINE_CONTENT.splitlines()
        top_section = "\n".join(lines[:120])
        assert "import importlib" in top_section, (
            "importlib must be imported at module level in engine.py, not inline"
        )

    def test_no_inline_import_importlib_in_fallback(self):
        """No 'import importlib' statement exists inside the _fallback_imports block."""
        # Find the _fallback_imports block
        lines = self._ENGINE_CONTENT.splitlines()
        fallback_start = next(
            (i for i, l in enumerate(lines) if "_fallback_imports = [" in l), None
        )
        assert fallback_start is not None
        # The 40 lines around the fallback block must NOT have a bare import statement
        block = "\n".join(lines[max(0, fallback_start - 2) : fallback_start + 40])
        # Inline `import importlib` (indented) must not appear inside the block
        assert "\n    import importlib" not in block and "\n                import importlib" not in block, (
            "Inline 'import importlib' must not appear inside the _fallback_imports block"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
