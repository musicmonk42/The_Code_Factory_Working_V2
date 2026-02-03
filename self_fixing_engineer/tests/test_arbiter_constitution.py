"""
Tests for arbiter/arbiter_constitution.py

- Robust import that works whether the module is at repo root or under 'arbiter/'.
- Validates parsing counts for each section.
- Verifies getters mirror parsed rules.
- Checks __str__ and __repr__ behaviors.
- Ensures logger is invoked on init.
- Handles malformed/empty constitution text gracefully.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


# -----------------------
# Hardened import shim
# -----------------------
def _import_constitution():
    import importlib

    candidates = ("self_fixing_engineer.arbiter.arbiter_constitution", "arbiter_constitution")
    for name in candidates:
        try:
            return importlib.import_module(name)
        except Exception:
            pass

    # Add repo root and 'arbiter/' to sys.path, then retry
    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    for p in (repo_root, repo_root / "arbiter"):
        sp = str(p)
        if sp not in sys.path:
            sys.path.insert(0, sp)

    for name in candidates:
        try:
            return importlib.import_module(name)
        except Exception:
            pass

    # Last resort: direct file load
    for f in (
        repo_root / "arbiter" / "arbiter_constitution.py",
        repo_root / "arbiter_constitution.py",
    ):
        if f.exists():
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "arbiter_constitution_fallback", str(f)
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)  # type: ignore[arg-type]
                return mod
    raise ImportError("Cannot import arbiter_constitution")


mod = _import_constitution()
ArbiterConstitution = getattr(mod, "ArbiterConstitution")
ARB_CONSTITUTION = getattr(mod, "ARB_CONSTITUTION")
logger = getattr(mod, "logger")


# -----------------------
# Tests
# -----------------------


def test_init_and_parse_counts():
    cons = ArbiterConstitution()
    assert cons.constitution_text == ARB_CONSTITUTION

    # Basic structure keys present
    assert set(cons.rules.keys()) == {
        "purpose",
        "powers",
        "principles",
        "evolution",
        "aim",
    }

    # Expected bullet counts (from the current constitution text)
    assert len(cons.rules["purpose"]) == 2
    assert len(cons.rules["powers"]) == 5
    assert len(cons.rules["principles"]) == 7
    assert len(cons.rules["evolution"]) == 2
    assert len(cons.rules["aim"]) == 1


def test_getters_match_rules():
    cons = ArbiterConstitution()
    assert cons.get_purpose() == cons.rules["purpose"]
    assert cons.get_powers() == cons.rules["powers"]
    assert cons.get_principles() == cons.rules["principles"]
    assert cons.get_evolution() == cons.rules["evolution"]
    assert cons.get_aim() == cons.rules["aim"]


def test_str_and_repr():
    cons = ArbiterConstitution()
    # __str__ should be the raw text
    assert str(cons) == ARB_CONSTITUTION

    # __repr__ includes hash of current text per implementation
    expected = f"ArbiterConstitution(hash={hash(cons.constitution_text)})"
    assert repr(cons) == expected


def test_logger_called_on_init():
    with patch.object(logger, "info") as mock_info:
        _ = ArbiterConstitution()
        assert mock_info.called


def test_malformed_text_graceful():
    malformed = "This has no recognizable section headers or bullets."
    with patch.object(mod, "ARB_CONSTITUTION", malformed):
        cons = ArbiterConstitution()
        # With no section headers, parser returns empty lists for each known key
        assert cons.rules == {
            "purpose": [],
            "powers": [],
            "principles": [],
            "evolution": [],
            "aim": [],
        }


def test_empty_text_graceful():
    with patch.object(mod, "ARB_CONSTITUTION", ""):
        cons = ArbiterConstitution()
        assert cons.rules == {
            "purpose": [],
            "powers": [],
            "principles": [],
            "evolution": [],
            "aim": [],
        }


def test_semantic_assertions_in_text():
    cons = ArbiterConstitution()
    assert any("core duty" in p for p in cons.get_purpose())
    assert any("autonomous access" in c for c in cons.get_powers())
    assert any("transparency" in p for p in cons.get_principles())
    assert any("propose constitutional amendments" in e for e in cons.get_evolution())
    assert any("goal is to serve" in a for a in cons.get_aim())
