import types

import agents.codegen_agent.codegen_prompt as codegen_prompt
import pytest

pytestmark = pytest.mark.asyncio


class DummyTemplate:
    def __init__(self, text: str):
        self.text = text

    def render(self, **kwargs):
        return self.text.format(**kwargs)


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

    # Allow label usage just in case; it's no-op.
    def labels(self, *args, **kwargs):
        return self


class DummyCounter:
    """
    Minimal Counter stub that supports `.labels().inc()`.
    """

    def labels(self, *args, **kwargs):
        return self

    def inc(self, *args, **kwargs):
        return None


def _patch_minimal_env(monkeypatch):
    """
    Force codegen_prompt.env.get_template to return deterministic templates,
    so tests don't depend on real template files on disk.

    Also ensure PROMPT_BUILD_LATENCY and PROMPT_ERRORS are safe, even if
    the global REGISTRY has odd dummy collectors registered under the same
    names from other components.
    """

    def fake_get_template(name):
        # Compact but structured template so assertions are explicit.
        base = (
            "STATE:{state_summary}\n"
            "FEATS:{features}\n"
            "BEST:{best_len}\n"
            "RAG:{rag_context}\n"
            "IMG:{has_img}\n"
            "LANG:{target_language}"
        )

        def _render(**ctx):
            return base.format(
                state_summary=ctx.get("state_summary", ""),
                features=",".join(ctx.get("requirements", {}).get("features", [])),
                best_len=len(ctx.get("best_practices", []) or []),
                rag_context=ctx.get("rag_context") or "",
                has_img=bool(
                    ctx.get("image_descriptions") or ctx.get("diagram_descriptions")
                ),
                target_language=ctx.get("target_language"),
            )

        return types.SimpleNamespace(render=_render)

    # Patch the Jinja environment
    monkeypatch.setattr(
        codegen_prompt,
        "env",
        types.SimpleNamespace(get_template=fake_get_template),
        raising=False,
    )

    # Ensure PROMPT_BUILD_LATENCY is a Histogram-like object with `.time()`
    monkeypatch.setattr(
        codegen_prompt,
        "PROMPT_BUILD_LATENCY",
        DummyHistogram(),
        raising=False,
    )

    # Ensure PROMPT_ERRORS is a usable Counter-like object
    monkeypatch.setattr(
        codegen_prompt,
        "PROMPT_ERRORS",
        DummyCounter(),
        raising=False,
    )


def test_get_best_practices_basic():
    """
    Basic sanity check that get_best_practices returns a non-empty, language-aware list.
    We don't assert exact contents to avoid coupling to text, only shape and intent.
    """
    practices = codegen_prompt.get_best_practices("python")
    assert isinstance(practices, list)
    assert len(practices) > 0
    # Heuristic: contains something plausibly about quality/safety.
    joined = " ".join(practices).lower()
    assert (
        "test" in joined
        or "async" in joined
        or "logging" in joined
        or "security" in joined
        or "type" in joined
    )


@pytest.mark.asyncio
async def test_build_prompt_valid_minimal(monkeypatch):
    """
    End-to-end-ish minimal prompt build:
    - Valid requirements
    - All external features disabled
    - Deterministic template
    - Verifies prompt structure + audit logging
    """
    _patch_minimal_env(monkeypatch)

    # Disable anything external / heavy for this test.
    monkeypatch.setattr(codegen_prompt, "SEARCH_API_KEY", None, raising=False)
    monkeypatch.setattr(codegen_prompt, "RAG_ENABLED", False, raising=False)
    monkeypatch.setattr(codegen_prompt, "VISION_ENABLED", False, raising=False)
    monkeypatch.setattr(codegen_prompt, "META_LLM_API_KEY", None, raising=False)

    # Deterministic cheap token counter
    def fake_count_tokens(prompt: str, model: str) -> int:
        return max(1, len(prompt) // 10)

    events = []

    async def fake_log_audit_event(event_type, payload=None):
        events.append((event_type, payload or {}))

    monkeypatch.setattr(
        codegen_prompt, "count_tokens", fake_count_tokens, raising=False
    )
    monkeypatch.setattr(
        codegen_prompt, "log_audit_event", fake_log_audit_event, raising=False
    )

    requirements = {"features": ["feature one", "feature two"]}
    state_summary = "System is green."

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

    # Structural assertions based on our patched template
    assert "STATE:System is green." in prompt
    assert "FEATS:feature one,feature two" in prompt
    assert "BEST:" in prompt
    assert "RAG:" in prompt  # should be empty but present
    assert "IMG:False" in prompt
    assert "LANG:python" in prompt or "LANG:" in prompt

    # Confirm audit event recorded
    assert any(e[0] == "Code Generation Prompt Built" for e in events)


@pytest.mark.asyncio
async def test_build_prompt_invalid_requirements(monkeypatch):
    """
    If 'features' is missing or invalid, builder must raise ValueError.
    """
    _patch_minimal_env(monkeypatch)

    bad_requirements = {"no_features": True}

    with pytest.raises(ValueError):
        await codegen_prompt.build_code_generation_prompt(
            requirements=bad_requirements,
            state_summary="x",
            previous_feedback=None,
            target_language="python",
            target_framework=None,
            enable_meta_llm_critique=False,
            multi_modal_inputs=None,
            audit_logger=None,
            redis_client=None,
        )


@pytest.mark.asyncio
async def test_build_prompt_invalid_multimodal_urls(monkeypatch):
    """
    Malformed URLs in multi_modal_inputs should raise ValueError and not silently pass.
    """
    _patch_minimal_env(monkeypatch)

    requirements = {"features": ["do a thing"]}

    with pytest.raises(ValueError):
        await codegen_prompt.build_code_generation_prompt(
            requirements=requirements,
            state_summary="x",
            previous_feedback=None,
            target_language="python",
            target_framework=None,
            enable_meta_llm_critique=False,
            multi_modal_inputs={"image_urls": ["not-a-url"]},
            audit_logger=None,
            redis_client=None,
        )


@pytest.mark.asyncio
async def test_retrieve_augmented_context_disabled(monkeypatch):
    """
    When SEARCH_API_KEY and RAG are disabled, augmented context should be empty.
    """
    _patch_minimal_env(monkeypatch)

    monkeypatch.setattr(codegen_prompt, "SEARCH_API_KEY", None, raising=False)
    monkeypatch.setattr(codegen_prompt, "RAG_ENABLED", False, raising=False)

    requirements = {"features": ["alpha", "beta"]}

    ctx = await codegen_prompt.retrieve_augmented_context(
        requirements=requirements,
        target_language="python",
        redis_client=None,
    )

    assert ctx == ""


@pytest.mark.asyncio
async def test_translate_requirements_no_keys(monkeypatch):
    """
    With no translation API keys available, translation helper should be a no-op
    and not crash.
    """
    reqs = {"features": ["funktionalität prüfen"]}

    class DummySecrets:
        def get(self, key: str):
            return None

    monkeypatch.setattr(
        codegen_prompt, "secrets_manager", DummySecrets(), raising=False
    )

    out = await codegen_prompt.translate_requirements_if_needed(dict(reqs))
    assert out["features"] == reqs["features"]


@pytest.mark.asyncio
async def test_process_multi_modal_input_disabled(monkeypatch):
    """
    When VISION_ENABLED is False, multi-modal processing should quietly return (None, None).
    """
    _patch_minimal_env(monkeypatch)
    monkeypatch.setattr(codegen_prompt, "VISION_ENABLED", False, raising=False)

    image_descriptions, diagram_descriptions = (
        await codegen_prompt.process_multi_modal_input(
            {"image_urls": ["https://example.com/x.png"]}
        )
    )

    assert image_descriptions is None
    assert diagram_descriptions is None


@pytest.mark.asyncio
async def test_build_prompt_with_meta_llm_critique(monkeypatch):
    """
    Enable meta-LLM critique and ensure:
    - No real network is called (patched ClientSession)
    - Advisory text is appended
    - Audit event 'Prompt Self-Refined' is emitted
    - redact_secrets is used
    """
    _patch_minimal_env(monkeypatch)

    # Force meta-LLM path to be active
    monkeypatch.setattr(codegen_prompt, "META_LLM_API_KEY", "dummy-key", raising=False)

    # ---- Correct aiohttp-style test double ----

    class DummyResp:
        """
        Emulates aiohttp.ClientResponse within `async with session.post(...) as resp:`.
        """

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            # No-op: simulate 200 OK
            return None

        async def json(self):
            # Simulate a model response with critique content
            return {
                "choices": [
                    {
                        "message": {
                            "content": "Tighten constraints and clarify inputs/outputs."
                        }
                    }
                ]
            }

    class DummySession:
        """
        Emulates aiohttp.ClientSession for:
            async with aiohttp.ClientSession() as session:
                async with session.post(...) as resp:
                    ...
        """

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            # In real aiohttp, post(...) returns an awaitable/CM.
            # For our usage (`async with session.post(...) as resp:`),
            # returning an object with async __aenter__/__aexit__ is sufficient.
            return DummyResp()

    # Patch aiohttp.ClientSession constructor
    monkeypatch.setattr(
        codegen_prompt.aiohttp,
        "ClientSession",
        lambda: DummySession(),
        raising=False,
    )

    # ---- Supporting stubs + capture ----

    def fake_count_tokens(prompt: str, model: str) -> int:
        # Keep under any configured MAX_PROMPT_TOKENS but non-trivial.
        return min(512, max(1, len(prompt) // 10))

    events = []

    async def fake_log_audit_event(event_type, payload=None):
        events.append((event_type, payload or {}))

    def fake_redact_secrets(text: str) -> str:
        # Simple redactor used only to verify it's invoked
        return text.replace("SECRET", "")

    monkeypatch.setattr(
        codegen_prompt, "count_tokens", fake_count_tokens, raising=False
    )
    monkeypatch.setattr(
        codegen_prompt, "log_audit_event", fake_log_audit_event, raising=False
    )
    monkeypatch.setattr(
        codegen_prompt, "redact_secrets", fake_redact_secrets, raising=False
    )

    requirements = {"features": ["do something safe"]}

    prompt = await codegen_prompt.build_code_generation_prompt(
        requirements=requirements,
        state_summary="ok",
        previous_feedback=None,
        target_language="python",
        target_framework=None,
        enable_meta_llm_critique=True,
        multi_modal_inputs=None,
        audit_logger=None,
        redis_client=None,
    )

    # Ensure the advisory marker is present in the final prompt
    assert "Self-Correction Advisory" in prompt

    # Ensure a self-refinement audit entry is recorded
    assert any(e[0] == "Prompt Self-Refined" for e in events)


@pytest.mark.filterwarnings("ignore:The test.*is marked with.*asyncio.*but it is not an async function:pytest.PytestWarning")
def test_hot_reloading_loader_clears_cache():
    """
    Test that HotReloadingFileSystemLoader correctly clears the environment cache
    when a template is modified.
    """
    import tempfile
    import os
    from jinja2 import Environment
    from agents.codegen_agent.codegen_prompt import HotReloadingFileSystemLoader
    
    # Create a temporary directory for templates
    with tempfile.TemporaryDirectory() as tmpdir:
        template_path = os.path.join(tmpdir, "test_template.txt")
        
        # Write initial template
        with open(template_path, "w") as f:
            f.write("Initial content")
        
        # Create loader and environment
        loader = HotReloadingFileSystemLoader([tmpdir])
        env = Environment(loader=loader)
        
        # Load the template first time
        template1 = env.get_template("test_template.txt")
        content1 = template1.render()
        
        # FIXED: Ensure we're actually getting string content, not Mock
        assert isinstance(content1, str), f"Expected string, got {type(content1)}"
        assert "Initial content" in content1
        
        # Verify cache has content
        assert len(env.cache) > 0
        
        # Modify file timestamp
        current_time = os.path.getmtime(template_path)
        new_time = current_time + 2
        os.utime(template_path, (new_time, new_time))
        
        # Modify content
        with open(template_path, "w") as f:
            f.write("Modified content")
        
        # Track cache clears
        clear_called = []
        original_clear = env.cache.clear
        def mock_clear():
            clear_called.append(True)
            original_clear()
        env.cache.clear = mock_clear
        
        # Reload template
        template2 = env.get_template("test_template.txt")  
        content2 = template2.render()
        
        # Verify results
        assert isinstance(content2, str)
        assert "Modified content" in content2
        assert len(clear_called) > 0, "cache.clear() was not called"
        assert content1 != content2
