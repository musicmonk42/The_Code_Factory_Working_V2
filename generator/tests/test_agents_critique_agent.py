import inspect
import json
from pathlib import Path
from typing import Any, Dict, Optional

import agents.critique_agent.critique_agent as core
import pytest
from agents.critique_agent.critique_agent import (
    CritiqueConfig,
    PythonCritiquePlugin,
    call_llm_for_critique,
    orchestrate_critique_pipeline,
)

# ---------------------------------------------------------------------------
# CritiqueConfig tests
# ---------------------------------------------------------------------------


def _safe_construct_config(**kwargs: Any) -> Optional[CritiqueConfig]:
    """
    Helper that encapsulates "reject OR normalize" semantics.

    If construction fails (raises), we return None.
    If it succeeds, caller can assert normalized values.
    """
    try:
        return CritiqueConfig(**kwargs)
    except Exception:
        return None


def test_critique_config_defaults_and_validation() -> None:
    """
    Baseline contract for CritiqueConfig.

    Expectations (must be stable for callers):
    - 'python' is included in languages.
    - pipeline_steps is a non-empty list and includes 'lint'.
    - max_retries is positive.
    - enable_vulnerability_scan is a boolean.
    - Obviously invalid pipeline steps are either rejected or sanitized.
    """
    cfg = CritiqueConfig()

    # Defaults sanity
    assert isinstance(cfg.languages, list)
    assert "python" in cfg.languages

    assert isinstance(cfg.pipeline_steps, list)
    assert cfg.pipeline_steps, "pipeline_steps must not be empty"
    assert "lint" in cfg.pipeline_steps

    assert isinstance(cfg.max_retries, int)
    assert cfg.max_retries >= 1

    assert isinstance(cfg.enable_vulnerability_scan, bool)

    # Invalid pipeline steps:
    # Contract: MUST NOT allow silent nonsense.
    # Either:
    #   - raise on construction, OR
    #   - normalize by dropping invalid values.
    bad = None
    try:
        bad = CritiqueConfig(pipeline_steps=["lint", "totally_invalid_step"])
    except Exception:
        bad = None

    if bad is not None:
        # If it didn't raise, it must have sanitized the step.
        assert "lint" in bad.pipeline_steps
        assert "totally_invalid_step" not in bad.pipeline_steps


def test_critique_config_rejects_or_normalizes_invalid_values() -> None:
    """
    Additional hardening for CritiqueConfig.

    Enterprise-style contract:
    - Negative / nonsensical values must not silently sneak through.
    - They must either be corrected to a safe default or rejected.
    """

    # Negative retries -> either rejected or clamped to non-negative
    cfg_neg_retries = _safe_construct_config(max_retries=-5)
    if cfg_neg_retries is not None:
        assert cfg_neg_retries.max_retries >= 0

    # Zero concurrency might be normalized up or rejected, but not left as a broken runtime footgun
    cfg_zero_conc = _safe_construct_config(max_parallel_steps=0)  # type: ignore[arg-type]
    if cfg_zero_conc is not None:
        assert cfg_zero_conc.max_parallel_steps >= 1

    # Nonsense language should be either dropped or cause validation failure
    cfg_weird_lang = _safe_construct_config(languages=["python", "___weird___"])
    if cfg_weird_lang is not None:
        assert "python" in cfg_weird_lang.languages
        assert "___weird___" not in cfg_weird_lang.languages


# ---------------------------------------------------------------------------
# call_llm_for_critique tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_llm_for_critique_parses_json_and_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    call_llm_for_critique must:
    - Call call_llm_api.
    - Correctly parse JSON in the response (including fenced ```json blocks).
    - Merge parsed JSON fields into the returned structure.
    """

    async def fake_call_llm_api(prompt: str, provider: str) -> Dict[str, Any]:
        assert "CRITIQUE" in prompt or prompt  # not too strict; just ensure it's called
        # Simulate fenced JSON from an LLM
        return {"content": """```json
{"verdict": "pass", "score": 0.99, "details": "looks good"}
```"""}

    monkeypatch.setattr(core, "call_llm_api", fake_call_llm_api, raising=False)

    cfg = CritiqueConfig(target_language="python")
    result = await call_llm_for_critique("CRITIQUE PROMPT", "semantic", cfg)

    assert isinstance(result, dict)
    # Parsed JSON fields must be present
    assert result.get("verdict") == "pass"
    assert result.get("score") == 0.99
    assert result.get("details") == "looks good"
    # Should also preserve at least the raw content or metadata from original response
    assert "content" in result or "raw_content" in result


@pytest.mark.asyncio
async def test_call_llm_for_critique_handles_bad_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    If the LLM returns non-JSON junk, call_llm_for_critique must:
    - Not raise.
    - Return a structured error indicator containing the raw content.
    """

    async def fake_call_llm_api(prompt: str, provider: str) -> Dict[str, Any]:
        return {"content": "NOT JSON AT ALL"}

    monkeypatch.setattr(core, "call_llm_api", fake_call_llm_api, raising=False)

    cfg = CritiqueConfig(target_language="python")
    result = await call_llm_for_critique("PROMPT", "semantic", cfg)

    assert isinstance(result, dict)
    # The function should not pretend parsing succeeded
    # It should surface that parsing failed somehow.
    assert "parse_error" in result or "error" in result
    assert result.get("raw_content", result.get("content", "")).startswith("NOT JSON")


# ---------------------------------------------------------------------------
# PythonCritiquePlugin: unit behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_python_plugin_lint_delegates_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    PythonCritiquePlugin.lint must:
    - Save provided files via save_files_to_output.
    - Delegate to run_all_lints_and_checks with correct args.
    - Be testable without hitting real linters / toolchain.
    """

    recorded: Dict[str, Any] = {"saved": False, "called": False}

    async def fake_save_files_to_output(files: Dict[str, str], outdir: Path) -> None:
        assert "main.py" in files
        assert outdir == tmp_path
        recorded["saved"] = True

    async def fake_run_all_lints_and_checks(
        *args: Any, **kwargs: Any
    ) -> Dict[str, Any]:
        # Expect first arg = code_files, second arg = project_dir (str)
        assert len(args) >= 2, "Expected code_files and project_dir as positional args"
        code_files = args[0]
        project_dir = args[1]

        assert isinstance(code_files, dict)
        assert "main.py" in code_files

        # project_dir may be Path or str depending on implementation; normalize
        assert str(project_dir) == str(tmp_path) or project_dir == str(tmp_path)

        # language may be in kwargs (parameter name is 'language', not 'lang')
        assert kwargs.get("language") == "python"

        # project_dir may also be present as kw; if so, it must match
        if "project_dir" in kwargs:
            assert str(kwargs["project_dir"]) == str(tmp_path)

        recorded["called"] = True
        return {"all_errors": []}

    monkeypatch.setattr(
        core, "save_files_to_output", fake_save_files_to_output, raising=False
    )
    monkeypatch.setattr(
        core, "run_all_lints_and_checks", fake_run_all_lints_and_checks, raising=False
    )

    plugin = PythonCritiquePlugin(CritiqueConfig())
    res = await plugin.lint({"main.py": "print('x')"}, tmp_path, CritiqueConfig())

    assert recorded["saved"] is True
    assert recorded["called"] is True
    assert isinstance(res, dict)
    assert "all_errors" in res


# ---------------------------------------------------------------------------
# Orchestration: high-level pipeline behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrate_critique_pipeline_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Pipeline-level contract test (happy path, fully stubbed):

    - Uses a config dict, as required by orchestrate_critique_pipeline.
    - Stubs resilient_step so each logical step "succeeds".
    - Asserts that the returned structure aggregates expected results.
    """

    flags = {
        "lint": False,
        "unit_test": False,
        "e2e_test": False,
        "stress_test": False,
        "security_scan": False,
        "semantic": False,
    }

    async def fake_resilient_step(
        func: Any,
        *args: Any,
        step_name: str,
        config: CritiqueConfig,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        # Simulate per-step success without invoking real tools
        if step_name == "lint":
            flags["lint"] = True
            return {"all_errors": []}
        if step_name == "unit_test":
            flags["unit_test"] = True
            return {"pass_rate": 1.0, "coverage_percentage": 100.0}
        if step_name == "e2e_test":
            flags["e2e_test"] = True
            return {"passed": True}
        if step_name == "stress_test":
            flags["stress_test"] = True
            return {"passed": True}
        if step_name == "security_scan":
            flags["security_scan"] = True
            return {"vulnerabilities": []}
        if step_name == "semantic":
            flags["semantic"] = True
            return {"score": 0.99, "summary": "ok"}
        # Default: behave neutrally
        return {}

    # Monkeypatch the orchestrator to use our stubbed step runner
    monkeypatch.setattr(core, "resilient_step", fake_resilient_step, raising=True)

    # Also stub detect_language and get_plugin so we don't depend on external mapping
    def fake_detect_language(code_files: Dict[str, str]) -> str:
        assert code_files
        return "python"

    class FakePlugin(PythonCritiquePlugin):
        # All methods unused thanks to fake_resilient_step, but keep shape valid
        pass

    def fake_get_plugin(language: str, config: CritiqueConfig) -> FakePlugin:
        assert language == "python"
        return FakePlugin(config)

    monkeypatch.setattr(core, "detect_language", fake_detect_language, raising=False)
    monkeypatch.setattr(core, "get_plugin", fake_get_plugin, raising=False)

    # Build config - use the object directly, not dict
    cfg = CritiqueConfig()

    code_files = {"main.py": "print('hello')"}
    test_files = {"test_main.py": "def test_ok(): assert 1 == 1"}
    requirements = {"summary": "hello must print"}
    state_summary = "pre"

    # Build kwargs dynamically from the actual function signature
    sig = inspect.signature(orchestrate_critique_pipeline)
    kwargs: Dict[str, Any] = {}
    for name in sig.parameters:
        if name == "code_files":
            kwargs[name] = code_files
        elif name == "test_files":
            kwargs[name] = test_files
        elif name == "requirements":
            kwargs[name] = requirements
        elif name == "state_summary":
            kwargs[name] = state_summary
        elif name == "config":
            kwargs[name] = cfg

    result = await orchestrate_critique_pipeline(**kwargs)  # type: ignore[arg-type]

    assert isinstance(result, dict)

    # We expect at least some top-level outcome fields propagated
    assert "provenance_chain" in result
    assert isinstance(result["provenance_chain"], list)

    # Our fake steps should have been invoked
    for step, hit in flags.items():
        assert hit, f"{step} step was not invoked in orchestrate_critique_pipeline"


@pytest.mark.asyncio
async def test_orchestrate_critique_pipeline_llm_failure_resilient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    If the 'semantic' step misbehaves, orchestrate_critique_pipeline must:
    - Not raise.
    - Still return a structured dict.
    - Preserve other step results where possible.
    """

    async def fake_resilient_step(
        func: Any,
        *args: Any,
        step_name: str,
        config: CritiqueConfig,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if step_name == "semantic":
            # Simulate LLM / parsing failure
            return {"error": "llm_failed", "raw_content": "nonsense"}
        if step_name == "lint":
            return {"all_errors": []}
        if step_name == "unit_test":
            return {"pass_rate": 1.0, "coverage_percentage": 100.0}
        if step_name == "security_scan":
            return {"vulnerabilities": []}
        # Other steps: neutral
        return {}

    monkeypatch.setattr(core, "resilient_step", fake_resilient_step, raising=True)

    def fake_detect_language(code_files: Dict[str, str]) -> str:
        return "python"

    class FakePlugin(PythonCritiquePlugin):
        pass

    def fake_get_plugin(language: str, config: CritiqueConfig) -> FakePlugin:
        return FakePlugin(config)

    monkeypatch.setattr(core, "detect_language", fake_detect_language, raising=False)
    monkeypatch.setattr(core, "get_plugin", fake_get_plugin, raising=False)

    cfg = CritiqueConfig()

    code_files = {"main.py": "print('hello')"}
    test_files = {"test_main.py": "def test_ok(): assert 1 == 1"}
    requirements = {"summary": "hello must print"}
    state_summary = "pre"

    sig = inspect.signature(orchestrate_critique_pipeline)
    kwargs: Dict[str, Any] = {}
    for name in sig.parameters:
        if name == "code_files":
            kwargs[name] = code_files
        elif name == "test_files":
            kwargs[name] = test_files
        elif name == "requirements":
            kwargs[name] = requirements
        elif name == "state_summary":
            kwargs[name] = state_summary
        elif name == "config":
            kwargs[name] = cfg

    result = await orchestrate_critique_pipeline(**kwargs)  # type: ignore[arg-type]

    assert isinstance(result, dict)
    # Even with semantic failure, the pipeline should be structurally sound
    assert "provenance_chain" in result
    # And we should surface some indication of the semantic error, not crash
    semantic_keys = json.dumps(result)
    assert "llm_failed" in semantic_keys or "semantic" in semantic_keys.lower()
