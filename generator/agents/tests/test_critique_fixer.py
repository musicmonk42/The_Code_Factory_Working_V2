import os

import pytest
from unittest.mock import AsyncMock, patch

# Force TESTING mode so critique_fixer uses safe local stubs where defined
os.environ.setdefault("TESTING", "1")

from agents.critique_agent.critique_fixer import (  # type: ignore
    FixHistory,
    LLMGenerateStrategy,
    RegexStrategy,
    get_file_id,
    hitl_review_fixes,
    safety_check_fix,
    security_check_fix,
)


def test_fix_history_push_undo_redo():
    fh = FixHistory("file1")
    fh.push("v1")
    fh.push("v2")
    fh.push("v3")

    assert fh.undo() == "v2"
    assert fh.undo() == "v1"
    assert fh.undo() is None  # cannot go past first
    assert fh.redo() == "v2"
    assert fh.redo() == "v3"
    assert fh.redo() is None  # cannot go past last


def test_get_file_id_deterministic():
    fid1 = get_file_id("path/to/file.py")
    fid2 = get_file_id("path/to/file.py")
    fid3 = get_file_id("path/to/other.py")
    assert fid1 == fid2
    assert fid1 != fid3


@pytest.mark.asyncio
async def test_regex_strategy_basic_replacement():
    strat = RegexStrategy()
    code = "print('hello world')"
    fix = {"pattern": "hello", "replacement": "hi"}
    fixed = await strat.apply_fix(code, fix, "python")
    assert "hi world" in fixed


@pytest.mark.asyncio
async def test_llm_generate_strategy_python_rename_variable(monkeypatch):
    """
    Uses the AST-based path; no external LLM needed.
    """
    strat = LLMGenerateStrategy()
    code = "x = 1\nprint(x)\n"
    fix_details = {"type": "rename_variable", "old_name": "x", "new_name": "y"}
    fixed = await strat.apply_fix(code, fix_details, "python")
    assert "y = 1" in fixed
    assert "print(y)" in fixed


def test_hitl_review_fixes_with_callback():
    fixes = {
        "main.py": [{"strategy": "regex", "fix": {"pattern": "a", "replacement": "b"}}]
    }

    def approve_all(f):
        return f

    approved = hitl_review_fixes(fixes, callback=approve_all)
    assert approved == fixes


@pytest.mark.asyncio
async def test_security_check_fix_all_clear():
    """
    In TESTING mode, underlying check_owasp_compliance / scan_for_vulnerabilities
    stubs should yield an all-clear result.
    """
    # Mock scan_for_vulnerabilities to return an all-clear result
    async def mock_scan(*args, **kwargs):
        return {"vulnerabilities": []}
    
    with patch("agents.critique_agent.critique_fixer.scan_for_vulnerabilities", new=mock_scan):
        ok = await security_check_fix({"main.py": "print('safe')"}, "python")
        assert ok is True


@pytest.mark.asyncio
async def test_safety_check_fix_all_tests_pass():
    """
    In TESTING mode, run_tests_in_sandbox stub returns pass_rate=1.0.
    """
    # Mock run_tests_in_sandbox to return pass_rate=1.0
    async def mock_run_tests(*args, **kwargs):
        return {"pass_rate": 1.0}
    
    with patch("agents.critique_agent.critique_fixer.run_tests_in_sandbox", new=mock_run_tests):
        ok = await safety_check_fix(
            code_files={"main.py": "print('x')"},
            test_files={"test_main.py": "def test_ok(): assert 1 == 1"},
            lang="python",
        )
        assert ok is True
