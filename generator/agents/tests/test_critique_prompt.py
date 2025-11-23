import os
import inspect
from typing import Any, Dict

import pytest

# Ensure we run in a testing-safe mode
os.environ.setdefault("TESTING", "1")

# Import the module under test in a stable way
import agents.critique_agent.critique_prompt as cp  # type: ignore


# Convenience handles
build_semantic_critique_prompt = cp.build_semantic_critique_prompt
PromptConfig = cp.PromptConfig


class _DummyCounter:
    """
    Minimal stand-in for Prometheus Counter/Histogram with labels().
    Accepts any labels/signatures to avoid tight coupling to metrics lib.
    """

    def labels(self, *_, **__):
        return self

    # For Counter-like
    def inc(self, *_args, **_kwargs):
        return None

    # For Histogram-like
    def observe(self, *_args, **_kwargs):
        return None


@pytest.fixture(autouse=True)
def patch_prompt_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Autouse fixture that hardens the environment around build_semantic_critique_prompt.

    We stub:
    - Language/translation utilities.
    - PII/scrubbing and summarization helpers.
    - Token counting and RAG.
    - Template auto-tuning and multimodal helpers.
    - Metrics + logging hooks.

    Goal: deterministic, side-effect-free tests that assert behavior rather than infra.
    """
    # --- Async utility stubs ---

    async def fake_detect_language(text: str) -> str:
        # Keep it simple & deterministic for tests
        return "en"

    async def fake_translate_text(text: str, target: str = "en") -> str:
        # Should not be meaningfully used when language is already 'en'
        return text

    async def fake_scrub_pii_and_secrets(text: str) -> str:
        # No-op scrubber for tests
        return text

    async def fake_summarize_text(text: str, max_length: int | None = None) -> str:
        # Stable, idempotent-ish "summarizer"
        if max_length is not None and len(text) > max_length:
            return text[:max_length]
        return text

    async def fake_count_tokens(text: str, model_name: str = "default") -> int:
        # Deterministic cheap token heuristic: whitespace split
        return len(text.split())

    async def fake_rag_retrieve(query: str) -> str:
        # Deterministic RAG stub
        return f"[RAG:{query}]"

    async def fake_auto_tune_template_based_on_feedback(
        template_content: str, feedback: str | None
    ) -> str:
        # For tests: don't mutate content; just prove it's called safely
        return template_content

    async def fake_incorporate_multi_modal_data(
        code_files: Dict[str, str], test_files: Dict[str, str]
    ) -> str:
        # No multimodal data in unit tests
        return ""

    # --- Logging stub ---

    def fake_log_action(event: str, details: Dict[str, Any] | None = None) -> None:
        # Intentionally ignore; we only assert that calls don't explode.
        return None

    # --- Apply patches on the critique_prompt module ---

    monkeypatch.setattr(cp, "detect_language", fake_detect_language, raising=False)
    monkeypatch.setattr(cp, "translate_text", fake_translate_text, raising=False)
    monkeypatch.setattr(cp, "scrub_pii_and_secrets", fake_scrub_pii_and_secrets, raising=False)
    monkeypatch.setattr(cp, "summarize_text", fake_summarize_text, raising=False)
    monkeypatch.setattr(cp, "count_tokens", fake_count_tokens, raising=False)
    monkeypatch.setattr(cp, "rag_retrieve", fake_rag_retrieve, raising=False)
    monkeypatch.setattr(
        cp,
        "auto_tune_template_based_on_feedback",
        fake_auto_tune_template_based_on_feedback,
        raising=False,
    )
    monkeypatch.setattr(
        cp,
        "incorporate_multi_modal_data",
        fake_incorporate_multi_modal_data,
        raising=False,
    )
    monkeypatch.setattr(cp, "log_action", fake_log_action, raising=False)

    # --- Metrics stubs ---
    # Accept any labels(...) signature to avoid tight coupling.
    monkeypatch.setattr(cp, "PROMPT_BUILDS", _DummyCounter(), raising=False)
    monkeypatch.setattr(cp, "PROMPT_LATENCY", _DummyCounter(), raising=False)


def _build_kwargs_for_signature(
    func,
    *,
    requirements: Dict[str, Any],
    code_files: Dict[str, str],
    test_files: Dict[str, str],
    state_summary: str,
    config: PromptConfig | Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Helper to construct kwargs based on the live function signature.

    This makes the tests resilient to minor, non-breaking changes
    (e.g., new optional parameters with defaults).
    """
    sig = inspect.signature(func)
    kwargs: Dict[str, Any] = {}

    for name, param in sig.parameters.items():
        if name == "requirements":
            kwargs[name] = requirements
        elif name == "code_files":
            kwargs[name] = code_files
        elif name == "test_files":
            kwargs[name] = test_files
        elif name == "state_summary":
            kwargs[name] = state_summary
        elif name == "config" and config is not None:
            kwargs[name] = config
        # For any future optional params, prefer to rely on defaults.
        # If a new *required* param appears, this will fail loudly,
        # forcing an explicit contract update in both code and tests.
        elif param.default is inspect._empty and name not in kwargs:
            raise AssertionError(
                f"New required parameter '{name}' detected in "
                f"{func.__name__} signature; tests must be updated to reflect the contract."
            )

    return kwargs


@pytest.mark.asyncio
async def test_build_semantic_critique_prompt_basic() -> None:
    """
    Baseline "industry-grade" contract:

    - Returns a non-empty string.
    - Incorporates core inputs (requirements, code, tests, state).
    - Reflects PromptConfig tasks / structure.
    - Executes successfully with all external dependencies stubbed.
    """
    cfg = PromptConfig()

    kwargs = _build_kwargs_for_signature(
        build_semantic_critique_prompt,
        requirements={"summary": "Ensure code matches requirements."},
        code_files={"main.py": "print('hello')"},
        test_files={"test_main.py": "def test_ok(): assert 1 == 1"},
        state_summary="Initial pipeline state",
        config=cfg,
    )

    prompt = await build_semantic_critique_prompt(**kwargs)

    assert isinstance(prompt, str)
    assert prompt.strip(), "Prompt must not be empty"

    # Must include some reflection of key inputs
    assert "Ensure code matches requirements." in prompt
    assert "main.py" in prompt
    assert "test_main.py" in prompt

    # Should encode at least one critique task / instruction from config
    for task in cfg.tasks:
        head = task.split(":")[0].strip()
        if head:
            assert head in prompt
            break

    # Should mention or provide structured critique guidance
    assert "critique" in prompt.lower() or "review" in prompt.lower()


@pytest.mark.asyncio
async def test_build_semantic_critique_prompt_deterministic_for_same_input() -> None:
    """
    For identical inputs, the prompt must be deterministic.

    This is critical for:
    - Caching
    - Auditing
    - Reproducibility in regulated environments
    """
    cfg = PromptConfig()

    base_kwargs = dict(
        requirements={"summary": "Same requirements"},
        code_files={"main.py": "print('x')"},
        test_files={"test_main.py": "def test_x(): pass"},
        state_summary="state",
        config=cfg,
    )

    kwargs1 = _build_kwargs_for_signature(build_semantic_critique_prompt, **base_kwargs)
    kwargs2 = _build_kwargs_for_signature(build_semantic_critique_prompt, **base_kwargs)

    p1 = await build_semantic_critique_prompt(**kwargs1)
    p2 = await build_semantic_critique_prompt(**kwargs2)

    assert isinstance(p1, str)
    assert isinstance(p2, str)
    assert p1 == p2, "Prompt must be stable for identical inputs"


@pytest.mark.asyncio
async def test_build_semantic_critique_prompt_changes_when_requirements_change() -> None:
    """
    Changing requirements must meaningfully change the prompt.

    Protects against:
    - Hard-coded templates ignoring caller input.
    - Silent regressions where requirements aren't wired through.
    """
    cfg = PromptConfig()

    def args_for(summary: str) -> Dict[str, Any]:
        return _build_kwargs_for_signature(
            build_semantic_critique_prompt,
            requirements={"summary": summary},
            code_files={"main.py": "print('x')"},
            test_files={"test_main.py": "def test_x(): pass"},
            state_summary="state",
            config=cfg,
        )

    strict_summary = "Enforce strict PEP8 and 100% test coverage"
    relaxed_summary = "Only perform a high-level design and readability critique"

    p_strict = await build_semantic_critique_prompt(**args_for(strict_summary))
    p_relaxed = await build_semantic_critique_prompt(**args_for(relaxed_summary))

    assert p_strict != p_relaxed, (
        "Prompt must change when high-sensitivity requirements change; "
        "otherwise consumers cannot trust the system to reflect inputs."
    )

    # Each prompt should contain its own requirement text
    assert strict_summary in p_strict
    assert relaxed_summary in p_relaxed
