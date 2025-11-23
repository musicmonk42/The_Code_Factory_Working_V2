# tests/test_import_fixer_integration.py
import asyncio
import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest


# -----------------------------------------------------------------------------
# Install core stubs BEFORE importing any fixer modules
# -----------------------------------------------------------------------------
def _install_core_stubs() -> None:
    """
    Provide minimal implementations for core_utils, core_audit, core_secrets so
    fixer_* modules can import successfully in a test sandbox.
    """
    # Create analyzer package and submodules
    if "analyzer" not in sys.modules:
        analyzer = types.ModuleType("analyzer")
        sys.modules["analyzer"] = analyzer

    # Create core_utils
    if "core_utils" not in sys.modules:
        fake_core_utils = types.SimpleNamespace(
            alert_operator=lambda msg, level="INFO": print(
                f"[OPS ALERT - {level}] {msg}"
            ),
            scrub_secrets=lambda x: x,
        )
        sys.modules["core_utils"] = fake_core_utils
        # Link it to the analyzer package
        analyzer_core_utils = types.ModuleType("analyzer.core_utils")
        analyzer_core_utils.alert_operator = sys.modules["core_utils"].alert_operator
        analyzer_core_utils.scrub_secrets = sys.modules["core_utils"].scrub_secrets
        sys.modules["analyzer.core_utils"] = analyzer_core_utils

    # Create core_audit with proper module structure
    if "core_audit" not in sys.modules:

        class _AuditLogger:
            def log_event(self, event_type: str, **kwargs):
                # Keep it simple and deterministic for tests
                print(f"[AUDIT_LOG] {event_type} {kwargs}")

        fake_core_audit = types.ModuleType("core_audit")
        fake_core_audit.audit_logger = _AuditLogger()
        fake_core_audit.get_audit_logger = lambda: fake_core_audit.audit_logger
        sys.modules["core_audit"] = fake_core_audit
        # Link it to the analyzer package
        analyzer_core_audit = types.ModuleType("analyzer.core_audit")
        analyzer_core_audit.audit_logger = fake_core_audit.audit_logger
        analyzer_core_audit.get_audit_logger = fake_core_audit.get_audit_logger
        sys.modules["analyzer.core_audit"] = analyzer_core_audit

    # Create core_secrets
    if "core_secrets" not in sys.modules:

        class _SecretsManager:
            def get_secret(self, key: str, required: bool = False):
                # Always return a stable dummy secret for tests
                return "dummy_secret"

        fake_core_secrets = types.SimpleNamespace(SECRETS_MANAGER=_SecretsManager())
        sys.modules["core_secrets"] = fake_core_secrets
        # Link it to the analyzer package
        analyzer_core_secrets = types.ModuleType("analyzer.core_secrets")
        analyzer_core_secrets.SECRETS_MANAGER = sys.modules[
            "core_secrets"
        ].SECRETS_MANAGER
        sys.modules["analyzer.core_secrets"] = analyzer_core_secrets


_install_core_stubs()


# -----------------------------
# Inline stubs (no conftest)
# -----------------------------
class FakeRedis:
    def __init__(self):
        self._store = {}

    def setex(self, key, ttl, value):
        self._store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self._store[key] = value
        return True

    def get(self, key):
        return self._store.get(key)

    async def get(self, key):
        return self._store.get(key)

    def incr(self, key):
        self._store[key] = int(self._store.get(key, 0) or 0) + 1
        return self._store[key]

    async def incr(self, key):
        self._store[key] = int(self._store.get(key, 0) or 0) + 1
        return self._store[key]

    async def ping(self):
        return True


class FakeLLMClient:
    def __init__(self, *a, **k):
        pass

    async def aclose(self):
        pass


class FakeAIManager:
    def __init__(self, *a, **k):
        self.http_client = object()
        self.llm_client = FakeLLMClient()

    async def generate_async(self, *a, **k) -> Dict[str, Any]:
        # Deterministic suggestion used by AST fixer when requested
        return {
            "suggestion": "Move import inside function to break cycle",
            "confidence": 0.92,
        }

    def generate_sync(self, *a, **k):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.generate_async(*a, **k))

    async def aclose(self):
        await self.llm_client.aclose()


class DummyProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, *a, **k):
        await asyncio.sleep(0)
        return (self._stdout.encode(), self._stderr.encode())


class PluginProbe:
    """Collects plugin hook invocations if the plugin manager calls them."""

    def __init__(self):
        self.calls: List[str] = []

    def __call__(self, *a, **k):
        self.calls.append(f"call:{k.get('hook') or a[0] if a else 'unknown'}")
        return True


# -----------------------------
# Module import helpers
# -----------------------------
def _import_by_candidates(
    name_candidates: List[str], file_candidates: List[Path]
) -> Any:
    for name in name_candidates:
        try:
            mod = importlib.import_module(name)
            sys.modules[name] = mod
            return mod
        except ModuleNotFoundError:
            continue
    for p in file_candidates:
        if p.exists():
            # Construct a full package path for the module
            for pkg in ["self_healing_import_fixer.import_fixer", "import_fixer"]:
                # Heuristic to find the package name from the file path
                if str(p).find(pkg.replace(".", os.sep)) != -1:
                    full_name = f"{pkg}.{p.stem}"
                    break
            else:
                full_name = f"tmpmod_{p.stem}"

            spec = importlib.util.spec_from_file_location(full_name, p)
            mod = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            # Register the module in sys.modules
            sys.modules[full_name] = mod
            spec.loader.exec_module(mod)
            return mod
    raise ImportError(
        f"Could not import any of: {name_candidates} or files: {file_candidates}"
    )


def load_import_fixer_modules(test_dir: Path) -> Dict[str, Any]:
    root = test_dir.parent
    cand_pkgs = [
        "self_healing_import_fixer.import_fixer",
        "import_fixer",
    ]

    # Build candidates per module
    def pkg_candidates(modname: str) -> List[str]:
        return [f"{pkg}.{modname}" for pkg in cand_pkgs]

    def file_candidates(rel: str) -> List[Path]:
        return [
            root / "self_healing_import_fixer" / "import_fixer" / rel,
            root / "import_fixer" / rel,
        ]

    modules = {}
    modules["engine"] = _import_by_candidates(
        pkg_candidates("import_fixer_engine"),
        file_candidates("import_fixer_engine.py"),
    )
    modules["validate"] = _import_by_candidates(
        pkg_candidates("fixer_validate"),
        file_candidates("fixer_validate.py"),
    )
    modules["plugins"] = _import_by_candidates(
        pkg_candidates("fixer_plugins"),
        file_candidates("fixer_plugins.py"),
    )
    modules["dep"] = _import_by_candidates(
        pkg_candidates("fixer_dep"),
        file_candidates("fixer_dep.py"),
    )
    modules["ast"] = _import_by_candidates(
        pkg_candidates("fixer_ast"),
        file_candidates("fixer_ast.py"),
    )
    modules["ai"] = _import_by_candidates(
        pkg_candidates("fixer_ai"),
        file_candidates("fixer_ai.py"),
    )
    return modules


# -----------------------------
# Tiny throwaway project
# -----------------------------
def make_tiny_project(base: Path) -> Path:
    proj = base / "proj"
    pkg = proj / "pkg"
    pkg.mkdir(parents=True)

    # a -> b
    (pkg / "__init__.py").write_text("VERSION='0.1.0'\n")
    (pkg / "a.py").write_text(
        "import pkg.b\n" "def greet():\n" "    return 'hi ' + pkg.b.name()\n"
    )
    # b -> a (cycle) + dynamic import pattern + third-party import
    (pkg / "b.py").write_text(
        "import requests\n"
        "def name():\n"
        "    from pkg.a import greet  # cycle\n"
        "    if False:\n"
        "        __import__('os')  # dynamic import\n"
        "    return 'world'\n"
    )

    # minimal pyproject (no deps initially)
    (proj / "pyproject.toml").write_text(
        "[project]\n" "name = 'tiny-proj'\n" "version = '0.1.0'\n" "dependencies = []\n"
    )
    return proj


# -----------------------------
# Helper: patch Redis + AI safely across modules
# -----------------------------
def _patch_infra(monkeypatch, modules: Dict[str, Any]) -> None:
    """
    Make all modules use FakeRedis and deterministic AI hooks regardless of how
    they reference redis or AI.
    """
    for m in modules.values():
        # Case A: module imported redis.asyncio as `redis` and calls redis.Redis(...)
        if hasattr(m, "redis"):
            # Replace m.redis.Redis with FakeRedis
            fake_redis_ns = types.SimpleNamespace(Redis=lambda *a, **k: FakeRedis())
            monkeypatch.setattr(m, "redis", fake_redis_ns, raising=False)

        # Case B: module exposes a module-level REDIS_CLIENT
        if hasattr(m, "REDIS_CLIENT"):
            monkeypatch.setattr(m, "REDIS_CLIENT", FakeRedis(), raising=False)

        # Rare Case C: someone did `from redis.asyncio import Redis` into attribute `Redis`
        if hasattr(m, "Redis"):
            monkeypatch.setattr(m, "Redis", lambda *a, **k: FakeRedis(), raising=False)

    # Patch AI hooks used by fixer_ast → fixer_ai
    ai_mod = modules.get("ai")
    if ai_mod:
        # Provide deterministic suggestion / patchers expected by fixer_ast wrapper
        monkeypatch.setattr(
            ai_mod,
            "get_ai_suggestions",
            lambda context: ["Move import inside function to break cycle"],
            raising=False,
        )
        monkeypatch.setattr(
            ai_mod,
            "get_ai_patch",
            lambda *args: ["# patch:\n# (no-op for test)"],
            raising=False,
        )


# -----------------------------
# The single integration test
# -----------------------------
@pytest.mark.integration
@pytest.mark.asyncio
async def test_import_fixer_stack_end_to_end(tmp_path, monkeypatch):
    # --- Build sandbox project
    proj_root = make_tiny_project(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # --- Safe dev env
    monkeypatch.setenv("PRODUCTION_MODE", "0")
    monkeypatch.setenv("SELF_HEALING_DEMO_MODE", "1")
    monkeypatch.setenv("APPROVED_OUTPUT_DIR", str(out_dir))
    # Some modules require UTF-8 env for subprocess behavior
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("LANG", "C.UTF-8")

    # --- Import modules (now that core stubs are installed)
    mods = load_import_fixer_modules(Path(__file__).resolve().parent)
    engine = mods["engine"]
    mods["validate"]
    mods["plugins"]
    mods["dep"]
    mods["ast"]
    mods["ai"]

    # --- Patch infra (Redis & AI hooks)
    _patch_infra(monkeypatch, mods)

    # --- Sanity: engine should expose a top-level function we can call
    assert hasattr(
        engine, "run_import_healer"
    ), "import_fixer_engine.run_import_healer missing"

    # Execute the "healing" against the tiny project.
    result = await engine.run_import_healer(
        project_root=str(proj_root),
        whitelisted_paths=[str(proj_root)],
        max_workers=4,
        dry_run=False,
        auto_add_deps=True,
        ai_enabled=True,
        output_dir=str(out_dir),
    )

    # Result contract (minimal checks for end-to-end):
    assert isinstance(result, dict)
    # Expect that dependencies may now include 'requests', or at least a proposal
    # and cycle handling created some action/log.
    # We don't over-specify; it's an integration smoke test.
    # Presence checks:
    assert "summary" in result or "report" in result or "actions" in result
