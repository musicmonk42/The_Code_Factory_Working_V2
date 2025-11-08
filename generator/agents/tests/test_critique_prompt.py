import os
import inspect
import pytest

# Ensure modules use testing-safe code paths where applicable
os.environ.setdefault("TESTING", "1")

from agents.critique_agent import build_semantic_critique_prompt, PromptConfig  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_build_semantic_critique_prompt_basic():
    """
    Smoke test: prompt builds successfully with minimal realistic inputs.
    Validates:
    - Non-empty string
    - Contains clear critique instructions / structure markers.
    """
    cfg = PromptConfig() if "PromptConfig" in globals() or hasattr(
        __import__("agents.critique_agent", fromlist=["PromptConfig"]), "PromptConfig"
    ) else None

    sig = inspect.signature(build_semantic_critique_prompt)
    kwargs = {}
    for name in sig.parameters:
        if name == "requirements":
            kwargs[name] = {"summary": "Ensure code matches requirements."}
        elif name == "code_files":
            kwargs[name] = {"main.py": "print('hello')"}
        elif name == "test_files":
            kwargs[name] = {"test_main.py": "def test_ok(): assert 1 == 1"}
        elif name == "state_summary":
            kwargs[name] = "Initial pipeline state"
        elif name == "config" and cfg is not None:
            kwargs[name] = cfg
        elif name == "multi_modal":
            kwargs[name] = None
        elif name == "user_context":
            kwargs[name] = {"actor": "unit-test"}
        elif name == "feedback":
            kwargs[name] = "Looks good so far."

    prompt = await build_semantic_critique_prompt(**kwargs)

    assert isinstance(prompt, str)
    assert len(prompt.strip()) > 0
    # Structural expectations: should mention requirements / code / tests.
    lc = prompt.lower()
    assert "requirement" in lc
    assert "code" in lc
    assert "test" in lc


@pytest.mark.asyncio
async def test_build_semantic_critique_prompt_deterministic_for_same_input():
    """
    Ensures deterministic prompt for identical inputs to support
    caching, audit, and reproducibility guarantees.
    """
    cfg = PromptConfig()
    sig = inspect.signature(build_semantic_critique_prompt)

    def build_args():
        kwargs = {}
        for name in sig.parameters:
            if name == "requirements":
                kwargs[name] = {"summary": "Same requirements"}
            elif name == "code_files":
                kwargs[name] = {"main.py": "print('x')"}
            elif name == "test_files":
                kwargs[name] = {"test_main.py": "def test_x(): pass"}
            elif name == "state_summary":
                kwargs[name] = "state"
            elif name == "config":
                kwargs[name] = cfg
        return kwargs

    p1 = await build_semantic_critique_prompt(**build_args())
    p2 = await build_semantic_critique_prompt(**build_args())

    assert p1 == p2
    assert len(p1.strip()) > 0


@pytest.mark.asyncio
async def test_build_semantic_critique_prompt_changes_when_requirements_change():
    """
    Changing requirements should meaningfully affect the prompt.
    Protects against accidental hard-coding or ignored inputs.
    """
    cfg = PromptConfig()
    sig = inspect.signature(build_semantic_critique_prompt)

    def args_for(summary: str):
        kwargs = {}
        for name in sig.parameters:
            if name == "requirements":
                kwargs[name] = {"summary": summary}
            elif name == "code_files":
                kwargs[name] = {"main.py": "print('x')"}
            elif name == "test_files":
                kwargs[name] = {"test_main.py": "def test_x(): pass"}
            elif name == "state_summary":
                kwargs[name] = "state"
            elif name == "config":
                kwargs[name] = cfg
        return kwargs

    p_strict = await build_semantic_critique_prompt(**args_for("Enforce strict PEP8 and 100% tests"))
    p_relaxed = await build_semantic_critique_prompt(**args_for("Only high-level design critique"))

    assert p_strict != p_relaxed
