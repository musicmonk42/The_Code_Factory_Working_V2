# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Structural tests for ensemble LLM client timeout and pipeline fixes.

Validates:
1. Per-provider timeout wrapping in LLMClient.call_ensemble_api()
2. Module-level call_ensemble_api() timeout_per_provider pass-through
3. ENSEMBLE_PROVIDER_TIMEOUT_SECONDS env-var configurability
4. Progress logging additions in codegen_agent multi-pass loop
5. PIPELINE_STEP_TIMEOUTS dict in omnicore_service
6. asyncio.wait_for wrapping around pipeline steps
7. Sentry DSN/environment fields in ServerConfig
8. DB_CONNECT_TIMEOUT default increase in codebase_analyzer
"""

import re
from pathlib import Path
from typing import List

import pytest


# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------

def _llm_client_src() -> str:
    return Path("generator/runner/llm_client.py").read_text(encoding="utf-8")


def _codegen_src() -> str:
    return Path("generator/agents/codegen_agent/codegen_agent.py").read_text(encoding="utf-8")


def _omnicore_src() -> str:
    return Path("server/services/omnicore_service.py").read_text(encoding="utf-8")


def _config_src() -> str:
    return Path("server/config.py").read_text(encoding="utf-8")


def _main_src() -> str:
    return Path("server/main.py").read_text(encoding="utf-8")


def _codebase_analyzer_src() -> str:
    return Path(
        "self_fixing_engineer/arbiter/codebase_analyzer.py"
    ).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Per-provider timeout in LLMClient.call_ensemble_api
# ---------------------------------------------------------------------------

class TestEnsembleProviderTimeout:
    """LLMClient.call_ensemble_api must wrap each task in asyncio.wait_for."""

    def test_provider_timeout_helper_method_exists(self):
        """A dedicated helper method must encapsulate the timeout logic."""
        src = _llm_client_src()
        assert "_call_llm_with_provider_timeout" in src, (
            "_call_llm_with_provider_timeout helper method not found in LLMClient; "
            "timeout logic must not be captured inside a loop closure"
        )

    def test_helper_has_docstring(self):
        """The helper method must be documented."""
        src = _llm_client_src()
        # The method must appear before a docstring (triple-quote)
        helper_idx = src.find("_call_llm_with_provider_timeout")
        assert helper_idx != -1
        nearby = src[helper_idx: helper_idx + 400]
        assert '"""' in nearby or "'''" in nearby, (
            "_call_llm_with_provider_timeout is missing a docstring"
        )

    def test_wait_for_used_in_method(self):
        src = _llm_client_src()
        assert "asyncio.wait_for(" in src, (
            "asyncio.wait_for not found in llm_client.py; per-provider timeout is missing"
        )

    def test_timeout_per_provider_parameter_exists(self):
        src = _llm_client_src()
        assert "timeout_per_provider" in src, (
            "timeout_per_provider parameter not found in call_ensemble_api"
        )

    def test_valid_models_used_in_error_loop(self):
        """Error-reporting loop must use valid_models, not the mutated models parameter."""
        src = _llm_client_src()
        assert "valid_models[idx]" in src, (
            "Error-reporting loop must index into valid_models (not the input models parameter)"
        )

    def test_models_parameter_not_reassigned(self):
        """The input models parameter must not be reassigned inside call_ensemble_api."""
        src = _llm_client_src()
        # Ensure 'models = valid_models' no longer appears (it was the previous bug)
        assert "models = valid_models" not in src, (
            "Input parameter 'models' must not be reassigned; use valid_models directly"
        )

    def test_timeout_per_provider_parameter_exists(self):
        src = _llm_client_src()
        assert "timeout_per_provider" in src, (
            "timeout_per_provider parameter not found in call_ensemble_api"
        )

    def test_env_var_name_present(self):
        src = _llm_client_src()
        assert "ENSEMBLE_PROVIDER_TIMEOUT_SECONDS" in src, (
            "ENSEMBLE_PROVIDER_TIMEOUT_SECONDS env-var name not found in llm_client.py"
        )

    def test_default_timeout_is_180(self):
        src = _llm_client_src()
        match = re.search(
            r'os\.environ\.get\s*\(\s*"ENSEMBLE_PROVIDER_TIMEOUT_SECONDS"\s*,\s*"(\d+)"\s*\)',
            src,
        )
        assert match, "ENSEMBLE_PROVIDER_TIMEOUT_SECONDS default not found via os.environ.get()"
        assert int(match.group(1)) == 180, (
            f"Default timeout should be 180s, got {match.group(1)}"
        )

    def test_timeout_error_logged(self):
        src = _llm_client_src()
        assert "[ENSEMBLE]" in src, "[ENSEMBLE] log prefix not found in llm_client.py"
        assert "timed out after" in src, (
            "Timeout log message 'timed out after' not found in llm_client.py"
        )

    def test_module_level_function_accepts_timeout(self):
        """Module-level call_ensemble_api must accept and forward timeout_per_provider."""
        src = _llm_client_src()
        # Locate the module-level (non-indented) async def — it starts at column 0.
        # We extract everything up to the first closing paren that terminates the
        # parameter list by scanning line-by-line from the signature start, which is
        # more robust than a single multiline regex on a 1200-line file.
        lines = src.splitlines()
        sig_lines: List[str] = []
        capturing = False
        paren_depth = 0
        for line in lines:
            if not capturing:
                if re.match(r'^async def call_ensemble_api\s*\(', line):
                    capturing = True
            if capturing:
                sig_lines.append(line)
                paren_depth += line.count("(") - line.count(")")
                if paren_depth <= 0:
                    break

        assert sig_lines, "Module-level call_ensemble_api function not found at column-0"
        func_sig = "\n".join(sig_lines)
        assert "timeout_per_provider" in func_sig, (
            "timeout_per_provider not in module-level call_ensemble_api signature"
        )

    def test_timeout_forwarded_to_method(self):
        """Module-level function must pass timeout_per_provider to the client method."""
        src = _llm_client_src()
        assert "timeout_per_provider=timeout_per_provider" in src, (
            "timeout_per_provider not forwarded from module-level function to LLMClient method"
        )


# ---------------------------------------------------------------------------
# 2. Progress logging in codegen_agent multi-pass loop
# ---------------------------------------------------------------------------

class TestMultiPassProgressLogging:
    """Multi-pass ensemble loop must log progress for each pass."""

    def test_starting_pass_log_present(self):
        src = _codegen_src()
        assert "starting pass" in src, (
            "'starting pass' log message not found in codegen_agent.py"
        )

    def test_pass_index_logged(self):
        src = _codegen_src()
        assert "_pass_index" in src, (
            "_pass_index variable not found; pass index not logged"
        )
        assert "len(_MULTIPASS_GROUPS)" in src, (
            "len(_MULTIPASS_GROUPS) not referenced in progress log"
        )

    def test_pass_start_timing(self):
        src = _codegen_src()
        assert "_pass_start = time.monotonic()" in src, (
            "_pass_start = time.monotonic() not found; pass timing is missing"
        )

    def test_pass_duration_logged_on_success(self):
        src = _codegen_src()
        assert "_pass_duration = time.monotonic() - _pass_start" in src, (
            "_pass_duration calculation not found"
        )
        assert "_pass_duration:.1f" in src, (
            "Pass duration not included in completion log message"
        )

    def test_pass_duration_logged_on_failure(self):
        src = _codegen_src()
        assert "failed after" in src, (
            "'failed after' not in warning log for failed pass"
        )

    def test_time_module_imported(self):
        src = _codegen_src()
        assert "import time" in src, (
            "'import time' not found in codegen_agent.py"
        )

    def test_two_occurrences_updated(self):
        """Both multi-pass ensemble blocks must contain the new logging."""
        src = _codegen_src()
        count = src.count("_pass_start = time.monotonic()")
        assert count >= 2, (
            f"Expected at least 2 occurrences of '_pass_start = time.monotonic()' "
            f"(one per multi-pass block), found {count}"
        )


# ---------------------------------------------------------------------------
# 3. Pipeline step timeouts in omnicore_service
# ---------------------------------------------------------------------------

class TestPipelineStepTimeouts:
    """Pipeline steps must be wrapped in asyncio.wait_for with PIPELINE_STEP_TIMEOUTS."""

    def test_pipeline_step_timeouts_dict_exists(self):
        src = _omnicore_src()
        assert "PIPELINE_STEP_TIMEOUTS" in src, (
            "PIPELINE_STEP_TIMEOUTS dict not found in omnicore_service.py"
        )

    def test_codegen_timeout_env_var(self):
        src = _omnicore_src()
        assert "PIPELINE_CODEGEN_TIMEOUT_SECONDS" in src, (
            "PIPELINE_CODEGEN_TIMEOUT_SECONDS not found"
        )

    def test_testgen_timeout_env_var(self):
        src = _omnicore_src()
        assert "PIPELINE_TESTGEN_TIMEOUT_SECONDS" in src, (
            "PIPELINE_TESTGEN_TIMEOUT_SECONDS not found"
        )

    def test_codegen_default_600(self):
        src = _omnicore_src()
        match = re.search(
            r'PIPELINE_CODEGEN_TIMEOUT_SECONDS["\s,]+(\d+)',
            src,
        )
        assert match, "PIPELINE_CODEGEN_TIMEOUT_SECONDS default value not found"
        assert int(match.group(1)) == 600, (
            f"Codegen timeout default should be 600s, got {match.group(1)}"
        )

    def test_wait_for_wraps_codegen(self):
        src = _omnicore_src()
        # The codegen step must use asyncio.wait_for
        assert "PIPELINE_STEP_TIMEOUTS[\"codegen\"]" in src or "PIPELINE_STEP_TIMEOUTS['codegen']" in src, (
            "PIPELINE_STEP_TIMEOUTS['codegen'] not referenced in pipeline"
        )

    def test_wait_for_wraps_testgen(self):
        src = _omnicore_src()
        assert (
            "PIPELINE_STEP_TIMEOUTS[\"testgen\"]" in src
            or "PIPELINE_STEP_TIMEOUTS['testgen']" in src
        ), "PIPELINE_STEP_TIMEOUTS['testgen'] not referenced in pipeline"

    def test_codegen_timeout_marks_job_failed(self):
        src = _omnicore_src()
        assert "_finalize_failed_job" in src, (
            "_finalize_failed_job not called after codegen timeout"
        )
        assert "timed out after" in src, (
            "'timed out after' message not found in timeout error handling"
        )

    def test_pipeline_timeout_logged_with_bracket_prefix(self):
        src = _omnicore_src()
        assert "[PIPELINE] Step" in src and "timed out after" in src, (
            "[PIPELINE] Step ... timed out log pattern not found"
        )


# ---------------------------------------------------------------------------
# 4. Sentry configuration in server/config.py
# ---------------------------------------------------------------------------

class TestSentryConfig:
    """ServerConfig must expose sentry_dsn and sentry_environment fields."""

    def test_sentry_dsn_field_present(self):
        src = _config_src()
        assert "sentry_dsn" in src, (
            "sentry_dsn field not found in server/config.py"
        )

    def test_sentry_environment_field_present(self):
        src = _config_src()
        assert "sentry_environment" in src, (
            "sentry_environment field not found in server/config.py"
        )

    def test_sentry_dsn_defaults_to_none(self):
        src = _config_src()
        # The field should default to None (optional)
        match = re.search(r'sentry_dsn\s*:\s*Optional\[str\]', src)
        assert match, "sentry_dsn should be Optional[str]"

    def test_sentry_sdk_init_in_main(self):
        src = _main_src()
        assert "sentry_sdk.init(" in src, (
            "sentry_sdk.init() call not found in server/main.py"
        )

    def test_sentry_environment_passed_to_sdk(self):
        src = _main_src()
        assert "environment=sentry_environment" in src, (
            "sentry_environment not passed to sentry_sdk.init()"
        )

    def test_sentry_import_error_handled(self):
        src = _main_src()
        assert "ImportError" in src and "sentry" in src.lower(), (
            "ImportError not handled when sentry-sdk is unavailable"
        )

    def test_sentry_traces_sample_rate_guarded(self):
        """Invalid SENTRY_TRACES_SAMPLE_RATE must not raise ValueError at startup."""
        src = _main_src()
        # The implementation must guard the float() conversion with a try/except
        assert "SENTRY_TRACES_SAMPLE_RATE" in src, (
            "SENTRY_TRACES_SAMPLE_RATE not referenced in server/main.py"
        )
        # Must have a ValueError catch near the sample-rate parsing
        assert "ValueError" in src and "SENTRY_TRACES_SAMPLE_RATE" in src, (
            "ValueError not caught when SENTRY_TRACES_SAMPLE_RATE has an invalid value"
        )

    def test_sentry_traces_sample_rate_fallback_logged(self):
        """A warning must be logged when SENTRY_TRACES_SAMPLE_RATE is unparseable."""
        src = _main_src()
        assert "Invalid SENTRY_TRACES_SAMPLE_RATE" in src, (
            "Warning for invalid SENTRY_TRACES_SAMPLE_RATE value not found in main.py"
        )


# ---------------------------------------------------------------------------
# 5. DB_CONNECT_TIMEOUT default increased to 15s
# ---------------------------------------------------------------------------

class TestCodebaseAnalyzerDbTimeout:
    """CodebaseAnalyzer must use a 15s default for DB_CONNECT_TIMEOUT."""

    def test_default_timeout_is_15(self):
        src = _codebase_analyzer_src()
        match = re.search(
            r'os\.getenv\s*\(\s*"DB_CONNECT_TIMEOUT"\s*,\s*"(\d+)"\s*\)',
            src,
        )
        assert match, "DB_CONNECT_TIMEOUT default not found via os.getenv()"
        assert int(match.group(1)) == 15, (
            f"DB_CONNECT_TIMEOUT default should be 15s, got {match.group(1)}"
        )

    def test_timeout_error_distinguished(self):
        src = _codebase_analyzer_src()
        # The timeout error message must mention both the timeout value and attempts
        assert "timed out after" in src, (
            "Timeout-specific log message not found in codebase_analyzer.py"
        )

    def test_connection_failure_logs_exception_type(self):
        src = _codebase_analyzer_src()
        # type(e).__name__ used in general failure log
        assert "type(e).__name__" in src, (
            "Exception type not logged in database connection failure message"
        )

    def test_default_comment_updated(self):
        src = _codebase_analyzer_src()
        assert "15s" in src, (
            "Comment for 15s default not found in codebase_analyzer.py"
        )

    def test_backoff_calculated_once_per_attempt(self):
        """Retry delay must be computed once per attempt, not duplicated per error branch."""
        src = _codebase_analyzer_src()
        # Previously the backoff was computed twice (once in TimeoutError handler,
        # once in general Exception handler).  The fix computes it once before the
        # try block.  We verify by checking that 'is_last_attempt' is used as the
        # guard (a single variable replaces two duplicated 'db_attempt < max_db_retries'
        # checks, each of which was preceded by a separate '_retry_delay =' assignment).
        assert "is_last_attempt" in src, (
            "is_last_attempt variable not found; backoff de-duplication may be missing"
        )
        # Each error branch must NOT independently recalculate _retry_delay
        retry_delay_assignments = src.count("_retry_delay = db_retry_delay * (2 **")
        assert retry_delay_assignments == 1, (
            f"Expected exactly 1 _retry_delay assignment (before the try block), "
            f"found {retry_delay_assignments}; backoff is still duplicated"
        )


# ---------------------------------------------------------------------------
# 6. Ensemble fallback mechanism (Requirement 1)
# ---------------------------------------------------------------------------

class TestEnsembleFallbackMechanism:
    """When all ensemble providers fail, call_ensemble_api must fall back to the
    single configured provider and include ``fallback_used: True`` in the result."""

    def test_fallback_provider_lookup_present(self):
        """Fallback must read the configured default provider from self.config."""
        src = _llm_client_src()
        assert "llm_provider" in src, (
            "Fallback must read config.llm_provider to determine the fallback provider"
        )

    def test_fallback_used_key_set_on_success(self):
        """A successful fallback must set ``fallback_used: True`` in the response."""
        src = _llm_client_src()
        assert '"fallback_used"' in src or "'fallback_used'" in src, (
            "fallback_used key not set in ensemble fallback response"
        )
        assert '_fb_result["fallback_used"] = True' in src or "_fb_result['fallback_used'] = True" in src, (
            "fallback_used must be set to True when ensemble falls back to single provider"
        )

    def test_fallback_logs_warning(self):
        """Fallback attempt must be logged at WARNING level with clear context."""
        src = _llm_client_src()
        assert "Attempting fallback to single provider" in src, (
            "'Attempting fallback to single provider' warning not found in llm_client.py"
        )

    def test_fallback_calls_call_llm_api(self):
        """Fallback must delegate to call_llm_api rather than re-entering the ensemble path."""
        src = _llm_client_src()
        # The fallback block must call self.call_llm_api (instance method on same client)
        assert "await self.call_llm_api(" in src, (
            "Fallback must call self.call_llm_api() — direct reuse of the already-initialized client"
        )

    def test_fallback_only_when_provider_available(self):
        """Fallback must be guarded: only attempt if the fallback provider is loaded."""
        src = _llm_client_src()
        assert "in available_providers" in src, (
            "Fallback guard 'in available_providers' not found; fallback may be attempted for unloaded providers"
        )

    def test_fallback_failure_logged_as_error(self):
        """If the fallback provider also fails, the error must be logged before re-raising."""
        src = _llm_client_src()
        assert "Fallback to single provider" in src and "also failed" in src, (
            "Error log for failed fallback provider not found in llm_client.py"
        )

    def test_skipped_providers_propagated_in_fallback_result(self):
        """The fallback result dict must include the original skipped_providers list."""
        src = _llm_client_src()
        # Verify the fallback path copies skipped_providers into the returned dict
        assert 'skipped_providers' in src, (
            "skipped_providers not propagated in fallback result"
        )

    def test_fallback_returns_dict_copy(self):
        """Fallback must return a copy of the result dict to avoid mutating cached data."""
        src = _llm_client_src()
        assert "dict(_fb_result)" in src or "_fb_result = dict(" in src, (
            "Fallback must copy the result dict before adding fallback_used key"
        )


# ---------------------------------------------------------------------------
# 7. Pre-flight provider health check (Requirement 3)
# ---------------------------------------------------------------------------

class TestEnsemblePreflightCheck:
    """_check_ensemble_readiness must fail fast when all providers have OPEN circuit breakers."""

    def test_method_exists(self):
        """LLMClient must expose _check_ensemble_readiness as an instance method."""
        src = _llm_client_src()
        assert "def _check_ensemble_readiness(" in src, (
            "_check_ensemble_readiness method not found in LLMClient"
        )

    def test_method_has_docstring(self):
        """_check_ensemble_readiness must be documented."""
        src = _llm_client_src()
        method_idx = src.find("def _check_ensemble_readiness(")
        assert method_idx != -1
        nearby = src[method_idx: method_idx + 500]
        assert '"""' in nearby, (
            "_check_ensemble_readiness is missing a docstring"
        )

    def test_circuit_breaker_state_queried(self):
        """Must read circuit breaker state for each provider before making network calls."""
        src = _llm_client_src()
        assert "circuit_breaker.get_state(" in src, (
            "circuit_breaker.get_state() not called in _check_ensemble_readiness"
        )

    def test_open_circuit_breaker_detected(self):
        """Must identify providers whose circuit breaker is in OPEN state."""
        src = _llm_client_src()
        assert '"OPEN"' in src or "'OPEN'" in src, (
            "OPEN state string not found in _check_ensemble_readiness"
        )

    def test_fails_fast_when_all_open(self):
        """Must raise LLMError immediately when all available providers are OPEN."""
        src = _llm_client_src()
        assert "All ensemble providers have open circuit breakers" in src, (
            "Fast-fail error message for all-OPEN circuit breakers not found"
        )

    def test_healthy_providers_tracked_separately(self):
        """Must separately track healthy providers to allow the call when at least one is healthy."""
        src = _llm_client_src()
        assert "healthy" in src, (
            "healthy provider list not maintained in _check_ensemble_readiness"
        )

    def test_readiness_log_emitted(self):
        """Provider readiness summary must be logged before each ensemble call."""
        src = _llm_client_src()
        assert "Provider readiness" in src, (
            "'Provider readiness' structured log not found in _check_ensemble_readiness"
        )

    def test_readiness_called_before_tasks_created(self):
        """_check_ensemble_readiness must be called before asyncio tasks are created."""
        src = _llm_client_src()
        # Use the concrete task-list expression (avoids matching docstring mentions)
        call_marker = "self._check_ensemble_readiness("
        task_marker = "asyncio.create_task(coro)"
        readiness_idx = src.find(call_marker)
        task_idx = src.find(task_marker)
        assert readiness_idx != -1, "self._check_ensemble_readiness() call not found"
        assert task_idx != -1, "asyncio.create_task(coro) list comprehension not found"
        assert readiness_idx < task_idx, (
            "_check_ensemble_readiness must be called before asyncio.create_task(); "
            "pre-flight check must happen before any network I/O is initiated"
        )

    def test_available_provider_filter_applied(self):
        """Providers not in available_providers must be excluded from the health check."""
        src = _llm_client_src()
        assert "not in available_providers" in src or "in available_providers" in src, (
            "available_providers guard not applied in _check_ensemble_readiness"
        )

    def test_failing_fast_raises_llm_error(self):
        """The fast-fail path must raise LLMError, not a generic Exception."""
        src = _llm_client_src()
        # Locate _check_ensemble_readiness and verify LLMError is raised within it
        method_start = src.find("def _check_ensemble_readiness(")
        method_end = src.find("\n    def ", method_start + 1)
        method_body = src[method_start:method_end] if method_end != -1 else src[method_start:]
        assert "raise LLMError(" in method_body, (
            "_check_ensemble_readiness must raise LLMError (not RuntimeError or generic Exception)"
        )


# ---------------------------------------------------------------------------
# 8. Improved logging at startup and per-provider status (Requirement 2)
# ---------------------------------------------------------------------------

class TestEnsembleDiagnosticLogging:
    """Ensemble calls must emit structured diagnostic logs at key decision points."""

    def test_available_providers_logged_before_ensemble(self):
        """Available providers must be logged before any provider is attempted."""
        src = _llm_client_src()
        assert "Available providers:" in src, (
            "'Available providers:' log not found in call_ensemble_api; "
            "must log which providers are loaded before attempting ensemble"
        )

    def test_requested_providers_logged(self):
        """The requested providers from the models list must be logged alongside available ones."""
        src = _llm_client_src()
        assert "requested:" in src or "requested providers" in src.lower(), (
            "Requested providers not logged alongside available providers in ensemble pre-flight"
        )

    def test_skipped_provider_warning_includes_available(self):
        """Skip warnings must include the available_providers list for easy diagnosis."""
        src = _llm_client_src()
        assert "Available providers:" in src, (
            "Available providers not included in provider-skip warning log"
        )

    def test_provider_readiness_summary_logged(self):
        """A readiness summary (healthy vs circuit-open) must be logged before each ensemble."""
        src = _llm_client_src()
        assert "circuit-open" in src, (
            "'circuit-open' not found in provider readiness log; "
            "structured readiness summary is required"
        )

    def test_ensemble_bracket_prefix_used_consistently(self):
        """All ensemble log messages must use the [ENSEMBLE] prefix for log filtering."""
        src = _llm_client_src()
        ensemble_log_count = src.count("[ENSEMBLE]")
        assert ensemble_log_count >= 6, (
            f"Expected at least 6 [ENSEMBLE]-prefixed log messages, found {ensemble_log_count}"
        )


# ---------------------------------------------------------------------------
# 9. Codegen agent ensemble fallbacks (Requirement 4)
# ---------------------------------------------------------------------------

class TestCodegenEnsembleFallbacks:
    """codegen_agent.py must degrade gracefully when ensemble calls fail."""

    def test_multipass_fallback_calls_call_llm_api(self):
        """Multi-pass except block must fall back to call_llm_api for the failed pass."""
        src = _codegen_src()
        assert "Attempting single-provider fallback" in src, (
            "'Attempting single-provider fallback' log not found in multi-pass except block"
        )

    def test_multipass_fallback_uses_config_backend(self):
        """Multi-pass fallback must use config.backend as the single provider."""
        src = _codegen_src()
        assert "provider=config.backend" in src, (
            "provider=config.backend not found in multi-pass single-provider fallback"
        )

    def test_multipass_fallback_logs_success(self):
        """Successful multi-pass fallback must log the number of files generated."""
        src = _codegen_src()
        assert "fallback succeeded" in src, (
            "'fallback succeeded' log not found after successful single-provider fallback in multi-pass"
        )

    def test_multipass_fallback_logs_failure_and_continues(self):
        """Failed multi-pass fallback must log and continue to the next pass (not abort)."""
        src = _codegen_src()
        assert "fallback also failed" in src, (
            "'fallback also failed' log not found; multi-pass must log final failure and continue"
        )

    def test_singlepass_wrapped_in_try_except(self):
        """Single-pass ensemble call must be wrapped in try/except for fallback support."""
        src = _codegen_src()
        assert "Single-pass ensemble failed" in src, (
            "'Single-pass ensemble failed' warning not found; single-pass must have fallback"
        )

    def test_singlepass_fallback_uses_config_backend(self):
        """Single-pass fallback must use the configured backend provider."""
        src = _codegen_src()
        assert "Single-provider fallback succeeded" in src, (
            "'Single-provider fallback succeeded' log not found in single-pass fallback path"
        )

    def test_two_multipass_fallback_occurrences(self):
        """Both multi-pass ensemble blocks (plugin and non-plugin branch) must have fallback."""
        src = _codegen_src()
        count = src.count("Attempting single-provider fallback")
        assert count >= 2, (
            f"Expected at least 2 occurrences of 'Attempting single-provider fallback' "
            f"(one per generate_code branch), found {count}"
        )

    def test_two_singlepass_fallback_occurrences(self):
        """Both single-pass ensemble blocks must have fallback logic."""
        src = _codegen_src()
        count = src.count("Single-pass ensemble failed")
        assert count >= 2, (
            f"Expected at least 2 occurrences of 'Single-pass ensemble failed' "
            f"(one per generate_code branch), found {count}"
        )

    def test_multipass_fallback_uses_pass_prompt(self):
        """Multi-pass fallback must use the pass-specific prompt, not the global prompt."""
        src = _codegen_src()
        assert "prompt=_pass_prompt" in src, (
            "Multi-pass fallback must send _pass_prompt (not the global prompt) to call_llm_api"
        )

    def test_multipass_fallback_merges_files(self):
        """Files produced by the fallback must be merged into _merged_files."""
        src = _codegen_src()
        # Verify the fallback path updates _merged_files (partial results tracked)
        assert "_merged_files.update(_pass_files)" in src, (
            "_merged_files.update not called after fallback; partial results not tracked"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
