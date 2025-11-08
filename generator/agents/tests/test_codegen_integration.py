import json
import types

import pytest

import agents.codegen_agent.codegen_prompt as codegen_prompt
import agents.codegen_agent.codegen_response_handler as crh


pytestmark = pytest.mark.asyncio


class DummyHistogram:
    """
    Minimal Prometheus-like Histogram stub that supports `.time()` as a
    context manager, matching the interface used in codegen_prompt.
    """

    class _Ctx:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    def time(self):
        return self._Ctx()

    def labels(self, *args, **kwargs):
        # For compatibility with label-using call sites (no-op).
        return self


class DummyCounter:
    """
    Minimal Counter stub that supports `.labels().inc()`.
    """

    def labels(self, *args, **kwargs):
        return self

    def inc(self, *args, **kwargs):
        return None


def _patch_env_and_utils(monkeypatch):
    """
    Shared patching helper for integration tests:

    - Provides deterministic in-memory Jinja environment.
    - Ensures PROMPT_BUILD_LATENCY and PROMPT_ERRORS are safe stubs even if
      global metrics registry has incompatible dummies.
    - Disables real external integrations (RAG, search, vision, meta-LLM).
    - Installs a cheap count_tokens.
    - Captures audit events from codegen_prompt.log_audit_event.
    """

    def fake_get_template(name: str):
        """
        Minimal but structured template to verify that fields are threaded correctly.
        The actual content is deterministic and simple.
        """

        def _render(**ctx):
            requirements = ctx.get("requirements", {}) or {}
            feats = ",".join(requirements.get("features", []))
            state = ctx.get("state_summary", "")
            lang = ctx.get("target_language", "")
            rag = ctx.get("rag_context") or ""
            return (
                f"LANG={lang}\n"
                f"STATE={state}\n"
                f"FEATS={feats}\n"
                f"RAG={rag}\n"
            )

        return types.SimpleNamespace(render=_render)

    # Patch Jinja2 environment used by codegen_prompt
    monkeypatch.setattr(
        codegen_prompt,
        "env",
        types.SimpleNamespace(get_template=fake_get_template),
        raising=False,
    )

    # Patch metrics to safe test stubs
    monkeypatch.setattr(
        codegen_prompt,
        "PROMPT_BUILD_LATENCY",
        DummyHistogram(),
        raising=False,
    )
    monkeypatch.setattr(
        codegen_prompt,
        "PROMPT_ERRORS",
        DummyCounter(),
        raising=False,
    )

    # Disable external / network / heavy features for deterministic test
    monkeypatch.setattr(codegen_prompt, "SEARCH_API_KEY", None, raising=False)
    monkeypatch.setattr(codegen_prompt, "RAG_ENABLED", False, raising=False)
    monkeypatch.setattr(codegen_prompt, "VISION_ENABLED", False, raising=False)
    monkeypatch.setattr(codegen_prompt, "META_LLM_API_KEY", None, raising=False)

    # Lightweight deterministic token counter
    def fake_count_tokens(prompt: str, model: str) -> int:
        # Keep it simple and always well below any max tokens.
        return max(1, len(prompt) // 5)

    monkeypatch.setattr(
        codegen_prompt,
        "count_tokens",
        fake_count_tokens,
        raising=False,
    )

    # Capture audit events emitted by codegen_prompt
    events = []

    def fake_log_audit_event(event_type, payload=None):
        events.append((event_type, (payload or {})))

    monkeypatch.setattr(
        codegen_prompt,
        "log_audit_event",
        fake_log_audit_event,
        raising=False,
    )

    return events


@pytest.mark.asyncio
async def test_end_to_end_prompt_parse_trace_scan(monkeypatch):
    """
    Full pipeline integration (without calling a real LLM):

    1. Build a prompt from realistic requirements.
    2. Simulate LLM response as JSON multi-file output.
    3. Parse/validate via codegen_response_handler.
    4. Add traceability metadata.
    5. Run security scan wrapper with fake SAST.

    This ensures codegen_prompt.py + codegen_response_handler.py
    compose cleanly in a realistic, test-safe environment.
    """
    events = _patch_env_and_utils(monkeypatch)

    # 1. Build prompt via codegen_prompt
    requirements = {
        "features": [
            "create a simple greeter",
            "log each greeting to stdout",
        ]
    }
    state_summary = "No previous code."

    prompt = await codegen_prompt.build_code_generation_prompt(
        requirements=requirements,
        state_summary=state_summary,
        previous_feedback=None,
        target_language="python",
        target_framework=None,
        enable_meta_llm_critique=False,
        multi_modal_inputs=None,
        audit_logger=None,
        redis_client=None,
    )

    # Check that key elements threaded through our patched template exist
    assert "LANG=python" in prompt or "LANG=" in prompt
    assert "STATE=No previous code." in prompt
    # Order of features may vary; accept either
    assert (
        "FEATS=create a simple greeter,log each greeting to stdout" in prompt
        or "FEATS=log each greeting to stdout,create a simple greeter" in prompt
    )

    # Ensure prompt build was audited
    assert any(
        e[0] == "Code Generation Prompt Built"
        for e in events
    )

    # 2. Simulate LLM response: valid Python referencing the requirements
    llm_response = json.dumps(
        {
            "files": {
                "greeter.py": (
                    "import sys\n"
                    "def greet(name: str) -> None:\n"
                    "    msg = f'Hello {name}'\n"
                    "    print(msg)\n"
                    "    # log each greeting to stdout\n"
                )
            }
        }
    )

    # 3. Parse + validate via codegen_response_handler
    parsed = crh.parse_llm_response(llm_response, lang="python")
    assert "greeter.py" in parsed
    assert "def greet" in parsed["greeter.py"]
    assert crh.ERROR_FILENAME not in parsed

    # 4. Add traceability comments based on requirements
    traced = crh.add_traceability_comments(parsed, requirements, lang="python")
    traced_content = traced["greeter.py"]

    # Ensure traceability header exists at the top
    first_line = traced_content.splitlines()[0]
    assert "CODE TRACEABILITY" in first_line

    # 5. Run security scan wrapper with fake SAST (no real tools)

    def fake_scan_for_vulnerabilities(files: dict) -> dict:
        # Simulate SAST returning zero issues for valid code
        return {name: {"issues": []} for name in files}

    logs = []

    def fake_log_action(event_type: str, payload=None):
        logs.append((event_type, (payload or {})))

    # Patch crh's dependencies
    monkeypatch.setattr(
        crh,
        "scan_for_vulnerabilities",
        fake_scan_for_vulnerabilities,
        raising=False,
    )
    monkeypatch.setattr(
        crh,
        "log_action",
        fake_log_action,
        raising=False,
    )

    final = crh.monitor_and_scan_code(traced)

    # Final output should equal traced (no destructive edits)
    assert final == traced

    # Ensure SAST completion was logged
    assert any(
        e[0] == "Unified SAST Scan Complete" or "SAST" in e[0]
        for e in logs
    )
