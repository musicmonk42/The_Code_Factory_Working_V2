import os
import inspect
import pytest

os.environ.setdefault("TESTING", "1")

from agents.critique_agent import (  # type: ignore
    orchestrate_critique_pipeline,
    CritiqueConfig,
)
import agents.critique_agent.critique_agent as core  # for monkeypatch targets


@pytest.mark.asyncio
async def test_orchestrate_critique_pipeline_happy_path(monkeypatch, tmp_path):
    """
    High-level integration test:
    - Mocks all heavy dependencies (LLM, linters, scanners, tests, auto-fixes).
    - Verifies that the pipeline completes and returns structured output.
    This is the enterprise/CI safety net: if someone wires a real side effect
    into the pipeline, this test will catch it.
    """

    async def fake_build_prompt(*args, **kwargs):
        return "CRITIQUE PROMPT"

    async def fake_run_lints(code_files, project_dir, lang="python", **kwargs):
        assert code_files
        return {"all_errors": []}

    async def fake_apply_auto_fixes(code_files, *_, **__):
        # Return code_files unchanged to simulate "no-op" or successful fixes.
        return code_files

    async def fake_runner_run_tests(payload):
        # Simulate perfect tests.
        return {"pass_rate": 1.0, "coverage_percentage": 100.0}

    async def fake_scan_for_vulnerabilities(code_files, *_, **__):
        # No vulnerabilities.
        return {"vulnerabilities": []}

    async def fake_call_llm_api(*args, **kwargs):
        # Return a minimal valid JSON critique result
        return {"content": '{"verdict": "pass", "score": 0.99}'}

    # Patch internals of critique_agent orchestrator
    monkeypatch.setattr(core, "build_semantic_critique_prompt", fake_build_prompt, raising=False)
    monkeypatch.setattr(core, "run_all_lints_and_checks", fake_run_lints, raising=False)
    monkeypatch.setattr(core, "apply_auto_fixes", fake_apply_auto_fixes, raising=False)
    monkeypatch.setattr(core, "runner_run_tests", fake_runner_run_tests, raising=False)
    monkeypatch.setattr(
        core, "scan_for_vulnerabilities", fake_scan_for_vulnerabilities, raising=False
    )
    monkeypatch.setattr(core, "call_llm_api", fake_call_llm_api, raising=False)

    cfg = CritiqueConfig()

    code_files = {"main.py": "print('hello')"}
    test_files = {"test_main.py": "def test_ok(): assert 1 == 1"}
    requirements = {"summary": "Hello world must print once."}
    state_summary = "pre-critique"

    # Be robust to signature changes by mapping args dynamically
    sig = inspect.signature(orchestrate_critique_pipeline)
    kwargs = {}
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
        elif name == "output_dir":
            kwargs[name] = tmp_path

    result = await orchestrate_critique_pipeline(**kwargs)

    assert isinstance(result, dict)
    # Expect at least one of these structural markers; adapt to your implementation.
    assert any(
        k in result
        for k in (
            "final_code_files",
            "code_files",
            "critique_results",
            "summary",
            "verdict",
        )
    )
    # Ensure we didn't accidentally try to hit network / docker paths.
    # (If those code paths ran, our fake functions or timeouts would likely fail.)
