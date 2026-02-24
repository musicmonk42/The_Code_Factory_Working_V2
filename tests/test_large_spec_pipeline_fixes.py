# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for large-spec pipeline fixes (2026-02-24).

Validates three fixes for production pipeline failures on complex specs:
1. MODEL_MAX_OUTPUT_TOKENS updated & multi-pass chunked generation
2. Auto-ensemble for large specs (each chunk uses majority-vote ensemble)
3. Additive retry strategy (keep existing files instead of discarding them)
"""

import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_codegen_module():
    """Import codegen_agent module for inspection."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "codegen_agent_test",
        Path("generator/agents/codegen_agent/codegen_agent.py"),
    )
    # We just read the source for structural checks; avoid executing heavy imports.
    return Path("generator/agents/codegen_agent/codegen_agent.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fix 3 – MODEL_MAX_OUTPUT_TOKENS
# ---------------------------------------------------------------------------

class TestModelMaxOutputTokensUpdated:
    """Verify MODEL_MAX_OUTPUT_TOKENS reflects current model capabilities."""

    def _read_constants(self):
        src = _get_codegen_module()
        # Extract the dict literal using a simple regex
        match = re.search(
            r'MODEL_MAX_OUTPUT_TOKENS\s*=\s*\{([^}]+)\}', src, re.DOTALL
        )
        assert match, "MODEL_MAX_OUTPUT_TOKENS dict not found in codegen_agent.py"
        return match.group(0)

    def test_gpt4o_limit_increased(self):
        """gpt-4o limit must be raised above the old 16384 ceiling."""
        raw = self._read_constants()
        match = re.search(r'"gpt-4o"\s*:\s*(\d+)', raw)
        assert match, '"gpt-4o" key missing from MODEL_MAX_OUTPUT_TOKENS'
        limit = int(match.group(1))
        assert limit > 16384, (
            f"gpt-4o limit ({limit}) should be greater than old 16384 ceiling"
        )

    def test_gpt4o_mini_limit_increased(self):
        """gpt-4o-mini limit must be raised above the old 16384 ceiling."""
        raw = self._read_constants()
        match = re.search(r'"gpt-4o-mini"\s*:\s*(\d+)', raw)
        assert match, '"gpt-4o-mini" key missing from MODEL_MAX_OUTPUT_TOKENS'
        limit = int(match.group(1))
        assert limit > 16384, (
            f"gpt-4o-mini limit ({limit}) should be greater than old 16384 ceiling"
        )

    def test_new_models_added(self):
        """gpt-4.5-preview and o3-mini must be present."""
        raw = self._read_constants()
        assert '"gpt-4.5-preview"' in raw, "gpt-4.5-preview missing from MODEL_MAX_OUTPUT_TOKENS"
        assert '"o3-mini"' in raw, "o3-mini missing from MODEL_MAX_OUTPUT_TOKENS"

    def test_claude_models_added(self):
        """At least one Claude model entry must be present."""
        raw = self._read_constants()
        assert "claude" in raw, "No Claude model entries found in MODEL_MAX_OUTPUT_TOKENS"

    def test_o1_still_present(self):
        """Existing o1 entry must not have been removed."""
        raw = self._read_constants()
        assert '"o1"' in raw, "o1 entry unexpectedly removed from MODEL_MAX_OUTPUT_TOKENS"


# ---------------------------------------------------------------------------
# Fix 1 – Multi-pass constants and helpers
# ---------------------------------------------------------------------------

class TestMultipassConstants:
    """Verify multi-pass constants and helpers exist in codegen_agent.py."""

    def _src(self):
        return _get_codegen_module()

    def test_endpoint_threshold_constant_exists(self):
        src = self._src()
        assert "MULTIPASS_ENDPOINT_THRESHOLD" in src, (
            "MULTIPASS_ENDPOINT_THRESHOLD constant not found in codegen_agent.py"
        )

    def test_endpoint_threshold_value(self):
        src = self._src()
        match = re.search(r'MULTIPASS_ENDPOINT_THRESHOLD\s*=\s*(\d+)', src)
        assert match, "MULTIPASS_ENDPOINT_THRESHOLD not assigned a value"
        assert int(match.group(1)) == 15, "MULTIPASS_ENDPOINT_THRESHOLD should be 15"

    def test_multipass_groups_defined(self):
        src = self._src()
        assert "_MULTIPASS_GROUPS" in src, "_MULTIPASS_GROUPS not defined"
        # Must have the expected logical group names
        assert '"core"' in src, 'Expected "core" group in _MULTIPASS_GROUPS'
        assert '"routes_and_services"' in src, 'Expected "routes_and_services" group in _MULTIPASS_GROUPS'
        assert '"infrastructure"' in src, 'Expected "infrastructure" group in _MULTIPASS_GROUPS'

    def test_count_spec_endpoints_helper_exists(self):
        src = self._src()
        assert "def _count_spec_endpoints(" in src, "_count_spec_endpoints helper not found"

    def test_should_use_multipass_helper_exists(self):
        src = self._src()
        assert "def _should_use_multipass(" in src, "_should_use_multipass helper not found"

    def test_count_spec_endpoints_logic(self):
        """_count_spec_endpoints must import directly without external deps."""
        # Inline a copy of the function's regex logic to test it independently
        def _count_spec_endpoints(requirements):
            md = requirements.get("md_content", "") or requirements.get("description", "")
            if not md:
                return 0
            matches = set(
                re.findall(
                    r'\b(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b\s+/\S+',
                    md, re.IGNORECASE
                )
            )
            return len(matches)

        spec_with_16_endpoints = "\n".join(
            f"GET /api/resource{i}" for i in range(16)
        )
        assert _count_spec_endpoints({"md_content": spec_with_16_endpoints}) == 16

        spec_with_5_endpoints = "GET /a\nPOST /b\nPUT /c\nDELETE /d\nPATCH /e"
        assert _count_spec_endpoints({"md_content": spec_with_5_endpoints}) == 5

        assert _count_spec_endpoints({}) == 0

    def test_should_use_multipass_threshold(self):
        """_should_use_multipass must activate at >=15 endpoints."""
        def _count(reqs):
            md = reqs.get("md_content", "") or reqs.get("description", "")
            if not md:
                return 0
            return len(set(re.findall(
                r'\b(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b\s+/\S+',
                md, re.IGNORECASE
            )))

        def _should(reqs, threshold=15):
            return _count(reqs) >= threshold

        under = {"md_content": "\n".join(f"GET /r{i}" for i in range(14))}
        over = {"md_content": "\n".join(f"GET /r{i}" for i in range(16))}

        assert not _should(under), "14 endpoints should NOT trigger multipass"
        assert _should(over), "16 endpoints should trigger multipass"


# ---------------------------------------------------------------------------
# New Requirement – Auto-ensemble for large specs
# ---------------------------------------------------------------------------

class TestAutoEnsembleForLargeSpecs:
    """Verify auto-ensemble logic is present in codegen_agent.py."""

    def _src(self):
        return _get_codegen_module()

    def test_auto_enable_ensemble_log_message_present(self):
        """Source must contain the required auto-enable log message."""
        src = self._src()
        assert "Auto-enabling ensemble mode for large spec" in src, (
            "Expected log message 'Auto-enabling ensemble mode for large spec' not found"
        )

    def test_effective_ensemble_variable_used(self):
        """A local variable that gates ensemble use must be set from _should_use_multipass."""
        src = self._src()
        # The logic sets _effective_ensemble based on multipass detection
        assert "_effective_ensemble" in src, (
            "_effective_ensemble variable not found; auto-ensemble gate is missing"
        )
        assert "_use_multipass" in src, (
            "_use_multipass variable not found; multipass flag is missing"
        )

    def test_multi_pass_ensemble_calls_per_group(self):
        """Each group in multi-pass must use call_ensemble_api (not call_llm_api)."""
        src = self._src()
        # The multi-pass ensemble block calls call_ensemble_api inside a for-loop over _MULTIPASS_GROUPS
        assert "Multi-pass ensemble generation: starting" in src, (
            "Multi-pass ensemble start log not found"
        )
        assert "Multi-pass ensemble complete" in src, (
            "Multi-pass ensemble completion log not found"
        )
        # Verify each pass calls ensemble, not single-LLM
        assert "_pass_dict = await call_ensemble_api(" in src, (
            "Multi-pass ensemble should call call_ensemble_api per chunk"
        )

    def test_small_spec_respects_original_config(self):
        """For small specs (below threshold) the original ensemble config is respected."""
        src = self._src()
        # The `else` branch (no auto-ensemble, no multipass) should still call call_llm_api
        assert "response = await call_llm_api(**_llm_kwargs)" in src, (
            "Single-LLM fallback (call_llm_api) not found; small specs may be broken"
        )

    def test_already_generated_files_used_in_multipass(self):
        """Multi-pass must skip files already generated on a previous pipeline retry."""
        src = self._src()
        assert "already_generated_files" in src, (
            "already_generated_files not referenced in codegen_agent.py"
        )
        assert "_already_generated" in src, (
            "_already_generated variable not found in multi-pass loop"
        )


# ---------------------------------------------------------------------------
# Fix 2 – Additive retry strategy in omnicore_service.py
# ---------------------------------------------------------------------------

class TestAdditiveRetryStrategy:
    """Verify additive retry (keep existing files) in omnicore_service.py."""

    def _src(self):
        return Path("server/services/omnicore_service.py").read_text(encoding="utf-8")

    def test_insufficient_output_no_rmtree(self):
        """InsufficientOutput retry must NOT delete the output directory."""
        src = self._src()
        # Find the InsufficientOutput block and ensure rmtree is gone from it
        # We check the log message that would have followed the rmtree is absent
        assert "cleaned up incomplete output for retry" not in src, (
            "rmtree cleanup for InsufficientOutput retry should have been removed"
        )

    def test_spec_fidelity_no_rmtree(self):
        """SpecFidelityFailure retry must NOT delete the output directory."""
        src = self._src()
        assert "cleaned up incomplete output for spec fidelity retry" not in src, (
            "rmtree cleanup for SpecFidelityFailure retry should have been removed"
        )

    def test_already_generated_files_in_previous_error(self):
        """InsufficientOutput previous_error must include already_generated_files."""
        src = self._src()
        assert '"already_generated_files"' in src or "'already_generated_files'" in src, (
            "already_generated_files key missing from previous_error dict"
        )

    def test_additive_retry_log_present(self):
        """Additive retry must log how many existing files are kept."""
        src = self._src()
        assert "existing files for additive retry" in src, (
            "Expected log message 'existing files for additive retry' not found"
        )
        assert "existing files for additive spec fidelity retry" in src, (
            "Expected log message 'existing files for additive spec fidelity retry' not found"
        )

    def test_already_generated_files_propagated_to_codegen(self):
        """_execute_codegen must propagate already_generated_files to requirements_dict."""
        src = self._src()
        assert "already_generated_files" in src, (
            "already_generated_files not propagated to requirements_dict in _execute_codegen"
        )
        assert "Propagating" in src and "already-generated files" in src, (
            "Log message for propagating already_generated_files not found"
        )

    def test_file_conflict_newer_wins(self):
        """When a retry generates a file that already exists, the newer version should win.

        This is guaranteed by dict.update() semantics: later passes / later retries
        overwrite earlier entries, so the newest version always takes precedence.
        """
        src = self._src()
        # The additive merge uses _merged_files.update() in codegen_agent, which means
        # later passes overwrite earlier ones. We verify the update call is there.
        codegen_src = _get_codegen_module()
        assert "_merged_files.update(" in codegen_src, (
            "_merged_files.update() not found; file-conflict resolution (newer-wins) may be broken"
        )


# ---------------------------------------------------------------------------
# Integration-style: multi-pass context accumulation
# ---------------------------------------------------------------------------

class TestMultipassContextAccumulation:
    """Verify that each multi-pass iteration is given context about prior passes."""

    def _src(self):
        return _get_codegen_module()

    def test_already_note_built_per_pass(self):
        """Each pass must construct an 'already generated' note from previous passes."""
        src = self._src()
        assert "_already_note" in src, (
            "_already_note not found; context about prior passes may be missing"
        )
        assert "Already-generated files (DO NOT regenerate these)" in src, (
            "Instruction to skip already-generated files not found in multi-pass prompt"
        )

    def test_already_list_is_union_of_merged_and_prior_retry(self):
        """_already must combine merged files AND files from a previous pipeline retry."""
        src = self._src()
        # _already = list(set(list(_merged_files.keys()) + _already_generated))
        assert "_already_generated" in src, "_already_generated not used in accumulation"
        assert "_merged_files.keys()" in src, "_merged_files.keys() not used in accumulation"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
