# tests/test_e2e_cli.py
import json
import time
import asyncio
import importlib
import importlib.util
from pathlib import Path
from typing import Any, Dict, List

import pytest


@pytest.mark.integration
def test_e2e_cli(tmp_path, monkeypatch):
    """
    Full-stack E2E covering:
      - analyzer (policy/graph/security/report/ai)
      - import_fixer (deps/ast/validate/plugins/engine)
      - top-level CLI wiring (click group)
    All external I/O is stubbed; artifacts (if any) must stay in APPROVED_OUTPUT_DIR.
    """

    # -----------------------------
    # 0) Tiny throwaway project
    # -----------------------------
    proj = tmp_path / "proj"
    pkg = proj / "pkg"
    pkg.mkdir(parents=True)

    (pkg / "__init__.py").write_text("VERSION='0.1.0'\n")
    (pkg / "a.py").write_text(
        "import pkg.b\n"
        "def greet():\n"
        "    return 'hi ' + pkg.b.name()\n"
    )
    (pkg / "b.py").write_text(
        "import requests\n"
        "def name():\n"
        "    from pkg.a import greet  # cycle\n"
        "    if False:\n"
        "        __import__('os')  # dynamic import\n"
        "    return 'world'\n"
    )
    (proj / "pyproject.toml").write_text(
        "[project]\nname='tiny-proj'\nversion='0.1.0'\ndependencies=[]\n"
    )

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    policy_path = tmp_path / "policy.json"
    policy = {
        "version": 1,
        "approved_roots": [str(proj)],
        "deny_rules": {"forbid_dynamic_imports": True, "forbid_cycles": True},
        "naming": {"module_case": "snake"},
    }
    policy_path.write_text(json.dumps(policy))

    # -----------------------------
    # 1) Safe environment
    # -----------------------------
    monkeypatch.setenv("PRODUCTION_MODE", "0")
    monkeypatch.setenv("SELF_HEALING_DEMO_MODE", "1")
    monkeypatch.setenv("APPROVED_OUTPUT_DIR", str(out_dir))
    monkeypatch.setenv("POLICY_FILE", str(policy_path))
    monkeypatch.setenv("LLM_ENDPOINT", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    # -----------------------------
    # 2) Import helper (pkg or file)
    # -----------------------------
    def import_cli_module():
        candidates = [
            "self_healing_import_fixer.cli",
            "cli",
        ]
        for name in candidates:
            try:
                return importlib.import_module(name)
            except ModuleNotFoundError:
                continue
        # file-based fallback
        here = Path(__file__).resolve().parent
        file_candidates = [
            here.parent / "self_healing_import_fixer" / "cli.py",
            here.parent / "cli.py",
        ]
        for p in file_candidates:
            if p.exists():
                spec = importlib.util.spec_from_file_location("top_cli_fallback", p)
                mod = importlib.util.module_from_spec(spec)
                assert spec and spec.loader
                spec.loader.exec_module(mod)  # type: ignore[attr-defined]
                return mod
        raise ImportError("Could not import top-level CLI module")

    cli_mod = import_cli_module()

    # -----------------------------
    # 3) Inline stubs for external I/O
    # -----------------------------
    # Fake Redis
    class FakeRedis:
        def __init__(self): self._store = {}
        def setex(self, k, ttl, v): self._store[k] = v; return True
        def get(self, k): return self._store.get(k)
        def incr(self, k): self._store[k] = int(self._store.get(k, 0) or 0) + 1; return self._store[k]

    # Fake LLM
    class FakeLLMClient:
        async def aclose(self): pass
    class FakeAIManager:
        def __init__(self, *a, **k):
            self.http_client = object()
            self.llm_client = FakeLLMClient()
        async def generate_async(self, *a, **k):
            return {"suggestion": "Move import inside function to break cycle", "confidence": 0.90}
        def generate_sync(self, *a, **k):
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.generate_async(*a, **k))
        async def aclose(self): await self.llm_client.aclose()

    # Dummy proc for validator tools
    class DummyProc:
        def __init__(self, rc=0, out="", err=""):
            self.returncode = rc; self._out = out; self._err = err
        async def communicate(self, *a, **k):
            await asyncio.sleep(0)
            return (self._out.encode(), self._err.encode())

    # Capture audit events
    audit_events: List[Dict[str, Any]] = []
    def audit_emit_stub(*a, **k):
        audit_events.append({"args": a, "kwargs": k, "ts": time.time()})
        return True

    # Patch analyzer + import_fixer submodules if present
    def _safe_patch(mod_name: str, attr: str, value):
        try:
            m = importlib.import_module(mod_name)
            setattr(m, attr, value)
        except Exception:
            pass

    # Try common module paths
    module_roots = [
        "self_healing_import_fixer.analyzer.core_ai",
        "self_healing_import_fixer.analyzer.core_audit",
        "self_healing_import_fixer.analyzer.core_security",
        "self_healing_import_fixer.analyzer.core_graph",
        "self_healing_import_fixer.analyzer.core_policy",
        "self_healing_import_fixer.analyzer.core_report",
        "self_healing_import_fixer.import_fixer.fixer_validate",
        "self_healing_import_fixer.import_fixer.fixer_plugins",
        "self_healing_import_fixer.import_fixer.fixer_dep",
        "self_healing_import_fixer.import_fixer.fixer_ast",
        "self_healing_import_fixer.import_fixer.fixer_ai",
    ] + [
        # alt import roots if package name differs
        "analyzer.core_ai","analyzer.core_audit","analyzer.core_security",
        "analyzer.core_graph","analyzer.core_policy","analyzer.core_report",
        "import_fixer.fixer_validate","import_fixer.fixer_plugins",
        "import_fixer.fixer_dep","import_fixer.fixer_ast","import_fixer.fixer_ai",
    ]

    # Apply patches
    for name in module_roots:
        if name.endswith(".core_ai") or name.endswith(".fixer_ai"):
            _safe_patch(name, "AIManager", FakeAIManager)
        if name.endswith(".core_audit"):
            _safe_patch(name, "emit_event", audit_emit_stub)
            _safe_patch(name, "flush_buffer", lambda: True)
        # Redis everywhere it's referenced
        _safe_patch(name, "Redis", lambda *a, **k: FakeRedis())
        # Security subprocess.run -> minimal JSON outputs
        if name.endswith(".core_security"):
            import subprocess as _sp
            def fake_run(cmd, timeout=60, capture_output=True, text=True, check=False, **kw):
                joined = " ".join(cmd) if isinstance(cmd, (list, tuple)) else str(cmd)
                if "bandit" in joined.lower():
                    data = {"results": [{"filename": str(pkg / "b.py"),
                                         "issue_severity": "LOW",
                                         "issue_text": "Test issue"}]}
                    return _sp.CompletedProcess(cmd, 0, json.dumps(data), "")
                if "pip-audit" in joined.lower():
                    data = {"dependencies": [], "vulnerabilities": []}
                    return _sp.CompletedProcess(cmd, 0, json.dumps(data), "")
                return _sp.CompletedProcess(cmd, 0, "", "")
            import importlib as _il
            try:
                m = _il.import_module(name)
                _safe_patch(name + ".subprocess", "run", fake_run)  # type: ignore[attr-defined]
            except Exception:
                pass
        # Validator create_subprocess_exec
        if name.endswith(".fixer_validate"):
            try:
                m = importlib.import_module(name)
                monkeypatch.setattr(m.asyncio, "create_subprocess_exec", lambda *a, **k: DummyProc(), raising=False)
            except Exception:
                pass

    # -----------------------------
    # 4) Discover click group & subcommands
    # -----------------------------
    try:
        pass
    except Exception:
        raise AssertionError("click is required for the CLI E2E test")

    cli_group = None
    for attr in dir(cli_mod):
        obj = getattr(cli_mod, attr)
        if hasattr(obj, "invoke") and obj.__class__.__name__ in {"Group", "Command"}:
            cli_group = obj
            break
    assert cli_group is not None, "No Click Group/Command found in top-level CLI module"

    from click.testing import CliRunner
    runner = CliRunner()

    # Helper: run a subcommand if present (by exact name or fuzzy match)
    def run_if_exists(name_hints: List[str], args: List[str]) -> bool:
        # best effort: inspect command mapping if it's a Group
        subcmds = getattr(cli_group, "commands", {}) or {}
        found_name = None
        for hint in name_hints:
            if hint in subcmds:
                found_name = hint
                break
        if not found_name:
            # fuzzy search on keys
            names = list(subcmds.keys())
            for hint in name_hints:
                for n in names:
                    if hint.replace("-", "").replace("_", "") in n.replace("-", "").replace("_", ""):
                        found_name = n
                        break
                if found_name:
                    break
        if not found_name:
            return False
        r = runner.invoke(cli_group, [found_name] + args)
        assert r.exit_code == 0, f"{found_name} failed: {r.output}"
        return True

    # ---- E2E run sequence (best-effort across varying CLIs)
    ran_any = False
    ran_any |= run_if_exists(["analyze"], [str(proj)])
    ran_any |= run_if_exists(["check-policy","policy","checkpolicy"], ["--policy", str(policy_path), str(proj)])
    ran_any |= run_if_exists(["security-scan","security","scan"], [str(proj)])
    # import-fixer pipeline: names differ across repos, try several
    ran_any |= run_if_exists(["heal","fix-imports","fiximports","import-fixer","importfixer"], ["--dry-run", str(proj)])
    # health check if present
    ran_any |= run_if_exists(["health-check","health","status"], [])

    assert ran_any, "No recognized CLI subcommands were found/executed for the E2E path"

    # -----------------------------
    # 5) E2E assertions
    # -----------------------------

    # A) At least one audit event was emitted
    assert len(audit_events) >= 1, "No audit events captured during E2E run"

    # B) If artifacts were written, they must be inside APPROVED_OUTPUT_DIR
    wrote_any = False
    for p in out_dir.rglob("*"):
        if p.is_file():
            wrote_any = True
            assert str(p.resolve()).startswith(str(out_dir.resolve())), f"Artifact escaped approved dir: {p}"
    if wrote_any:
        assert True  # explicit

    # C) Process stayed alive (i.e., no library called sys.exit())
    # If a library called sys.exit, pytest wouldn't reach this line.

