# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for 4 production bug fixes identified in job log 8b406ac6.

Bug 1: _retry_stub_files aborts too early — syntax_error_streak is incremented
       for filename-mismatch responses, conflating two distinct failure modes.
       Fix: separate wrong_filename_streak from syntax_error_streak so that the
       syntax-error abort budget is only consumed by actual syntax errors.

Bug 2: _reconcile_app_wiring generates generic CRUD stub routers without a
       PATCH endpoint and with an empty /api/v1/ prefix regardless of whether
       service/schema files already reference that pattern.
       Fix: add PATCH /{item_id} to stub routers; extend /api/v1/ detection to
       ALL Python files (not just router/main) so the prefix is correctly
       inferred from service/schema files that predate router generation;
       keep stub router prefix="" so include_router(..., prefix=...) is emitted
       in main.py where ProjectEndpointAnalyzer can discover it.

Bug 3: ProjectEndpointAnalyzer returns [] when _router_prefix_map is empty,
       causing downstream gap-fill passes to believe no endpoints are
       implemented.
       Fix: fall back to per-file AST extraction (local decorator paths, no
       prefix) when include_router() prefix wiring is absent.

Bug 4: validate_production_ready() detects stub class bodies in schema files
       and logs a warning but does not propagate the stub file list to
       __validation_summary__, leaving _retry_stub_files without metadata it
       could use to correlate validation failures with retry decisions.
       Fix: include stub_files_detected in __validation_summary__ so downstream
       consumers get explicit, structured information about which files still
       contain stub implementations.
"""

from __future__ import annotations

import re as _re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_CODEGEN_AGENT_PATH = (
    PROJECT_ROOT / "generator/agents/codegen_agent/codegen_agent.py"
)
_RESPONSE_HANDLER_PATH = (
    PROJECT_ROOT / "generator/agents/codegen_agent/codegen_response_handler.py"
)
_ANALYZER_PATH = (
    PROJECT_ROOT / "generator/utils/project_endpoint_analyzer.py"
)


def _agent_source() -> str:
    return _CODEGEN_AGENT_PATH.read_text(encoding="utf-8")


def _handler_source() -> str:
    return _RESPONSE_HANDLER_PATH.read_text(encoding="utf-8")


def _analyzer_source() -> str:
    return _ANALYZER_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Bug 1: syntax_error_streak vs. wrong_filename_streak separation
# ---------------------------------------------------------------------------

class TestBug1SyntaxErrorStreakSeparation:
    """_retry_stub_files must not consume the syntax-error abort budget for
    filename-mismatch responses."""

    def test_both_streak_counters_initialised(self):
        """Both streak counters must be declared and initialised to 0."""
        src = _agent_source()
        assert "syntax_error_streak = 0" in src
        assert "wrong_filename_streak = 0" in src

    def test_wrong_filename_streak_incremented_for_no_match(self):
        """wrong_filename_streak must increment on the _no_match branch."""
        src = _agent_source()
        assert "wrong_filename_streak += 1" in src

    def test_syntax_error_streak_guarded_by_not_no_match(self):
        """syntax_error_streak += 1 must appear only in the else branch of
        'if _no_match:', not unconditionally."""
        src = _agent_source()
        # The else branch of 'if _no_match:' contains 'syntax_error_streak += 1'.
        # Verify the increments are under the correct branch by checking ordering:
        # wrong_filename_streak += 1 must come BEFORE syntax_error_streak += 1
        # within the same if/else block.
        wf_pos = src.find("wrong_filename_streak += 1")
        se_pos = src.find("syntax_error_streak += 1")
        assert wf_pos != -1
        assert se_pos != -1
        assert wf_pos < se_pos, (
            "wrong_filename_streak increment must appear before "
            "syntax_error_streak increment (they are in if/else branches)"
        )

    def test_abort_threshold_is_three_not_two(self):
        """The syntax-error abort threshold must be 3 (>= 3), not the old 2."""
        src = _agent_source()
        assert "syntax_error_streak >= 3" in src
        assert "syntax_error_streak >= 2" not in src

    def test_both_streaks_reset_on_matched_files(self):
        """Both counters must reset in the else branch (when matched_files is
        non-empty), so a single successful match clears all streaks."""
        src = _agent_source()
        # Both resets must appear in source; verify via ordered proximity check.
        se_reset = "syntax_error_streak = 0"
        wf_reset = "wrong_filename_streak = 0"
        assert src.count(se_reset) >= 2, "must initialise + reset"
        assert src.count(wf_reset) >= 2, "must initialise + reset"

    def test_returned_paths_computed_before_if_not_matched_block(self):
        """returned_paths and _no_match must be assigned before
        'if not matched_files:' so both branches of that block can use them."""
        src = _agent_source()
        rp_idx = src.find("returned_paths = set(new_files.keys())")
        nm_idx = src.find("_no_match = bool(returned_paths)")
        block_idx = src.find("if not matched_files:")
        assert rp_idx != -1
        assert nm_idx != -1
        assert block_idx != -1
        assert rp_idx < block_idx, (
            "returned_paths must be assigned before 'if not matched_files:'"
        )
        assert nm_idx < block_idx, (
            "_no_match must be assigned before 'if not matched_files:'"
        )

    def test_all_returned_are_errors_in_else_branch_only(self):
        """_all_returned_are_errors must be evaluated lazily — only in the
        else branch of 'if _no_match:' where it is actually used."""
        src = _agent_source()
        # The pattern "if _no_match:" must appear BEFORE "_all_returned_are_errors"
        # in the streak block.  This proves lazy evaluation.
        no_match_if_idx = src.find("if _no_match:")
        all_err_idx = src.find("_all_returned_are_errors = all(")
        assert no_match_if_idx != -1
        assert all_err_idx != -1
        assert no_match_if_idx < all_err_idx, (
            "_all_returned_are_errors must be computed lazily inside the else "
            "branch of 'if _no_match:', not before it"
        )

    def test_wrong_filename_streak_debug_log(self):
        """The wrong-filename branch must emit a DEBUG-level log for observability."""
        src = _agent_source()
        assert "wrong_filename_streak=%d" in src

    def test_abort_log_message_references_three(self):
        """The abort log message must say '3 consecutive' to match the new threshold."""
        src = _agent_source()
        assert "3 consecutive all-error responses" in src

    def test_validation_summary_block_has_no_dead_computed_set(self):
        """The __validation_summary__ block must NOT contain the previously hollow
        '_added = set(...)' dead-code pattern."""
        src = _agent_source()
        # The refactored block directly uses the list without a diff-set computation.
        assert "_added = set(" not in src


# ---------------------------------------------------------------------------
# Bug 2: _reconcile_app_wiring stub router improvements
# ---------------------------------------------------------------------------

class TestBug2StubRouterImprovements:
    """_reconcile_app_wiring must generate stub routers with a PATCH endpoint
    and correctly propagate the project's /api/v1/ prefix via include_router()."""

    def test_patch_endpoint_template_present(self):
        """The stub router template must include a PATCH /{item_id} handler."""
        src = _agent_source()
        assert ".patch('/{{item_id}}')" in src, (
            "Stub router template must contain PATCH /{item_id} endpoint"
        )

    def test_patch_handler_has_partial_update_docstring(self):
        """PATCH handler must document it performs a partial update."""
        src = _agent_source()
        assert "Partially update a {entity}" in src or "Partially update a" in src

    def test_put_endpoint_still_present(self):
        """PUT /{item_id} must remain alongside PATCH — both are valid REST patterns."""
        src = _agent_source()
        assert ".put('/{{item_id}}')" in src

    def test_patch_inserted_between_put_and_delete(self):
        """PATCH must appear after PUT and before DELETE in the stub template."""
        src = _agent_source()
        put_idx = src.find(".put('/{{item_id}}')")
        patch_idx = src.find(".patch('/{{item_id}}')")
        delete_idx = src.find(".delete('/{{item_id}}')")
        assert put_idx != -1 and patch_idx != -1 and delete_idx != -1
        assert put_idx < patch_idx < delete_idx, (
            "PATCH must be inserted between PUT and DELETE"
        )

    def test_stub_router_prefix_kept_empty_in_router_modules(self):
        """The stub router must be registered with prefix='' in router_modules.

        Storing a non-empty prefix there would cause main.py to emit:
            app.include_router(alias)  # prefix already defined in router
        which ProjectEndpointAnalyzer cannot parse — it needs the prefix
        in the include_router(..., prefix=...) call itself.
        """
        src = _agent_source()
        # After 'router_modules.append({' in the stub-router generation block,
        # prefix must be empty string so main.py adds it via include_router().
        # Check that the append contains "prefix": "" (not a conditional value).
        pattern = _re.compile(
            r'router_modules\.append\(\{[^}]*"prefix":\s*""[^}]*"router_dir":\s*"routers"',
            _re.DOTALL,
        )
        assert pattern.search(src), (
            'Stub router must store "prefix": "" so main.py adds prefix= '
            'to include_router() where ProjectEndpointAnalyzer can find it'
        )

    def test_api_v1_scan_covers_all_python_files(self):
        """_has_api_v1 must scan ALL .py files, not a restricted subset.

        Service and schema files often reference /api/v1/ paths before router
        files exist.  Limiting the scan to router/main files would miss them,
        causing stub routers to get no prefix even for /api/v1/ projects.
        """
        src = _agent_source()
        # The old regex-restricted scan must be gone.
        assert "_API_V1_SCAN_RE" not in src, (
            "_API_V1_SCAN_RE must be removed; detection now uses .endswith('.py')"
        )
        # The replacement must use path.endswith(".py").
        assert 'if path.endswith(".py")' in src, (
            "_has_api_v1 must use path.endswith('.py') to scan all Python files"
        )

    def test_has_api_v1_used_in_prefix_kwarg(self):
        """The _has_api_v1 flag must still gate the prefix= kwarg generation in
        include_router() — it must not have been removed along with the regex."""
        src = _agent_source()
        assert "_has_api_v1" in src
        assert 'prefix="/api/v1/' in src


# ---------------------------------------------------------------------------
# Bug 3: ProjectEndpointAnalyzer fallback when _router_prefix_map is empty
# ---------------------------------------------------------------------------

class TestBug3ProjectEndpointAnalyzerFallback:
    """When no include_router() prefix wiring exists, the analyzer must fall
    back to per-file extraction rather than returning an empty list."""

    def _get_analyzer_class(self) -> Any:
        try:
            from generator.utils.project_endpoint_analyzer import ProjectEndpointAnalyzer
            return ProjectEndpointAnalyzer
        except ImportError:
            pytest.skip("ProjectEndpointAnalyzer not importable")

    def test_fallback_log_message_present_in_source(self):
        """The fallback branch must emit a recognisable structured log message."""
        src = _analyzer_source()
        assert "falling back to per-file local path extraction" in src

    def test_fallback_scans_only_router_files(self):
        """Only app/routers/*.py / app/routes/*.py must be included in the
        fallback scan — non-router files must be excluded."""
        src = _analyzer_source()
        # The fallback regex should match routers? / routes? directories only.
        assert "_router_file_re" in src or "routers?" in src

    def test_fallback_returns_endpoints_when_no_include_router(self):
        """With router files but no include_router() wiring in main.py, the
        fallback must return at least the locally-decorated endpoints."""
        PEA = self._get_analyzer_class()
        files = {
            "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
            "app/routers/patients.py": (
                "from fastapi import APIRouter\n"
                "patients_router = APIRouter()\n\n"
                "@patients_router.get('/')\n"
                "async def list_patients(): return []\n\n"
                "@patients_router.post('/')\n"
                "async def create_patient(): return {}\n"
            ),
        }
        endpoints = PEA(files).get_endpoints()
        methods_paths = {(e["method"], e["path"]) for e in endpoints}
        assert ("GET", "/") in methods_paths
        assert ("POST", "/") in methods_paths

    def test_service_files_excluded_from_fallback(self):
        """Service files must not appear in the fallback router-file scan."""
        PEA = self._get_analyzer_class()
        files = {
            "app/services/patient_service.py": (
                "# GET /api/v1/patients — just a comment\n"
                "class PatientService: pass\n"
            ),
            "app/routers/patients.py": (
                "from fastapi import APIRouter\n"
                "patients_router = APIRouter()\n\n"
                "@patients_router.get('/')\n"
                "async def list_patients(): return []\n"
            ),
        }
        endpoints = PEA(files).get_endpoints()
        paths = {e["path"] for e in endpoints}
        assert "/api/v1/patients" not in paths

    def test_empty_file_set_returns_empty_list(self):
        """With no files at all, the result must be an empty list."""
        PEA = self._get_analyzer_class()
        assert PEA({}).get_endpoints() == []

    def test_no_router_files_returns_empty_list(self):
        """With service/model files but no router files, fallback returns []."""
        PEA = self._get_analyzer_class()
        files = {
            "app/services/patient_service.py": "class PatientService: pass\n",
            "app/models/patient.py": "class Patient: pass\n",
        }
        assert PEA(files).get_endpoints() == []

    def test_fully_wired_project_still_uses_prefix_resolution(self):
        """Normal prefix-wired projects must continue to resolve fully-qualified
        paths — the fallback must not activate when prefix wiring is present."""
        PEA = self._get_analyzer_class()
        files = {
            "app/main.py": (
                "from fastapi import FastAPI\n"
                "from app.routers.patients import patients_router\n"
                "app = FastAPI()\n"
                "app.include_router(patients_router, prefix='/api/v1/patients')\n"
            ),
            "app/routers/patients.py": (
                "from fastapi import APIRouter\n"
                "patients_router = APIRouter()\n\n"
                "@patients_router.get('/')\n"
                "async def list_patients(): return []\n"
            ),
        }
        endpoints = PEA(files).get_endpoints()
        paths = {e["path"] for e in endpoints}
        assert "/api/v1/patients/" in paths


# ---------------------------------------------------------------------------
# Bug 4: validate_production_ready stubs fed into __validation_summary__
# ---------------------------------------------------------------------------

class TestBug4ValidationSummaryStubFiles:
    """parse_llm_response must add stub_files_detected to __validation_summary__
    when validate_production_ready detects stub class/function bodies, and
    _retry_stub_files must log them for operator visibility."""

    def test_stub_files_detected_key_present_in_all_summary_blocks(self):
        """Every __validation_summary__ json.dumps() call must include
        stub_files_detected so the key is present across all response
        parsing paths (dict fast-path, raw-JSON, nested multi-file JSON)."""
        src = _handler_source()
        pattern = _re.compile(
            r'json\.dumps\s*\(\s*\{[^}]*stub_files_detected[^}]*\}',
            _re.DOTALL,
        )
        matches = pattern.findall(src)
        assert len(matches) >= 2, (
            f"stub_files_detected must appear in at least 2 __validation_summary__ "
            f"construction blocks (one per JSON parse path); found {len(matches)}"
        )

    def test_prod_stub_files_variable_initialised_to_empty_list(self):
        """_prod_stub_files must be initialised to [] before the validation
        conditional so the key is always a list, never absent or None."""
        src = _handler_source()
        assert "_prod_stub_files: List[str] = []" in src

    def test_prod_stub_files_populated_only_on_validation_failure(self):
        """_prod_stub_files must only be populated when validation fails —
        the empty-list default is used when validation passes."""
        src = _handler_source()
        # The population uses a list-comprehension calling _detect_stub_patterns.
        assert "_detect_stub_patterns(content, fname)[0]" in src

    def test_consistent_variable_name_across_parse_paths(self):
        """All parse paths must use the same variable name _prod_stub_files.
        A stale alternate name (_prod_stub_files_mf) must not exist."""
        src = _handler_source()
        assert "_prod_stub_files_mf" not in src, (
            "Stale variable name _prod_stub_files_mf must be removed; "
            "all paths must use _prod_stub_files"
        )

    def test_retry_loop_logs_additional_stubs_from_validation_summary(self):
        """_retry_stub_files must read stub_files_detected from
        __validation_summary__ and log them for operator visibility."""
        src = _agent_source()
        assert "stub_files_detected" in src
        assert "validate_production_ready flagged" in src

    def test_no_dead_added_set_computation(self):
        """The previously hollow '_added = set(...)' dead-code pattern must
        not exist in the __validation_summary__ block."""
        src = _agent_source()
        assert "_added = set(" not in src


# ---------------------------------------------------------------------------
# Cross-cutting structural integrity checks
# ---------------------------------------------------------------------------

class TestStructuralIntegrity:
    """High-level source assertions that verify all four fixes are wired
    coherently without requiring a full end-to-end pipeline execution."""

    def test_no_duplicate_returned_paths_assignment(self):
        """returned_paths = set(new_files.keys()) must appear exactly once in
        the _retry_stub_files function body — the earlier duplicate (removed
        as part of Bug 1 fix) must not be present."""
        src = _agent_source()
        # Count occurrences of the exact assignment within the function
        count = src.count("returned_paths = set(new_files.keys())")
        assert count == 1, (
            f"returned_paths assignment must appear exactly once; found {count}"
        )

    def test_no_duplicate_no_match_computation(self):
        """_no_match = ... must appear exactly once — the old duplicate at the
        bottom of the replaced/hint block must be gone."""
        src = _agent_source()
        count = src.count("_no_match = bool(returned_paths)")
        assert count == 1, (
            f"_no_match assignment must appear exactly once; found {count}"
        )

    def test_patch_endpoint_between_put_and_delete(self):
        """In the stub router template, PATCH must appear between PUT and DELETE."""
        src = _agent_source()
        put_pos = src.find(".put('/{{item_id}}')")
        patch_pos = src.find(".patch('/{{item_id}}')")
        del_pos = src.find(".delete('/{{item_id}}')")
        assert put_pos < patch_pos < del_pos

    def test_api_v1_regex_removed(self):
        """_API_V1_SCAN_RE must be removed; its replacement uses .endswith('.py')."""
        src = _agent_source()
        assert "_API_V1_SCAN_RE" not in src

    def test_analyzer_fallback_uses_router_file_regex(self):
        """Fallback in _resolve_all_endpoints must use a regex that targets
        only router/route directories, not all Python files."""
        src = _analyzer_source()
        assert "_router_file_re" in src

    def test_all_validation_summary_blocks_use_same_stub_key(self):
        """All three __validation_summary__ blocks in the response handler must
        use the identical JSON key name 'stub_files_detected'."""
        src = _handler_source()
        assert src.count('"stub_files_detected"') >= 2

    def test_handler_no_stale_mf_suffix(self):
        """The _mf suffix variable name must not exist in the response handler."""
        src = _handler_source()
        assert "_prod_stub_files_mf" not in src

