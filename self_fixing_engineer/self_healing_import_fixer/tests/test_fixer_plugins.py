import hashlib
import hmac
import os
import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Bootstrap minimal infra modules BEFORE importing the system under test
# ---------------------------------------------------------------------------
core_utils = types.ModuleType("core_utils")


def _alert_operator(msg, level="INFO"):  # pragma: no cover
    return None


def _scrub_secrets(x):  # pragma: no cover
    return x


core_utils.alert_operator = _alert_operator
core_utils.scrub_secrets = _scrub_secrets
sys.modules["core_utils"] = core_utils

core_audit = types.ModuleType("core_audit")


class _AuditLogger:  # pragma: no cover
    def log_event(self, *a, **k):
        return None


core_audit.audit_logger = _AuditLogger()
sys.modules["core_audit"] = core_audit

core_secrets = types.ModuleType("core_secrets")


class _SecretsMgr:  # pragma: no cover
    def get_secret(self, *a, **k):
        return "test_hmac_key"


core_secrets.SECRETS_MANAGER = _SecretsMgr()
sys.modules["core_secrets"] = core_secrets

# Make package importable (tests/ sibling to import_fixer/)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import after dummies are in place
from import_fixer.fixer_plugins import (  # noqa: E402
    AnalyzerCriticalError,
    NonCriticalError,
    PluginManager,
    PluginValidationError,
    _get_plugin_signature_key,
    _reset_plugin_key_for_tests,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_plugin_file(dirpath: Path, module_name: str, body: str) -> Path:
    """Write a plugin file with given module name and body."""
    p = dirpath / f"{module_name}.py"
    p.write_text(body, encoding="utf-8")
    return p


def sha256_hex(key: bytes, data: bytes) -> str:
    return hmac.new(key, data, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # Default to non-production unless a test opts in
    monkeypatch.delenv("PRODUCTION_MODE", raising=False)
    # Reset cached HMAC key between tests
    _reset_plugin_key_for_tests()
    yield


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    d = tmp_path / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def hmac_key(monkeypatch):
    # Ensure a deterministic HMAC key even in production-mode tests
    monkeypatch.setattr(
        "import_fixer.fixer_plugins.SECRETS_MANAGER.get_secret",
        lambda *a, **k: "test_hmac_key",
        raising=True,
    )
    _reset_plugin_key_for_tests()
    return _get_plugin_signature_key(production_mode=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_plugin_manager_init_success(plugin_dir):
    mgr = PluginManager({"whitelisted_plugin_dirs": [str(plugin_dir)]})
    # tuple of resolved Paths internally; compare by string set
    assert set(map(str, mgr.whitelisted_plugin_dirs)) == {str(plugin_dir.resolve())}


def test_plugin_manager_init_no_whitelisted_dirs_in_prod_raises_error(monkeypatch):
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    with pytest.raises(AnalyzerCriticalError, match="whitelisted_plugin_dirs"):
        PluginManager({"whitelisted_plugin_dirs": []})


@pytest.mark.asyncio
async def test_load_plugin_lazy_loading_success(plugin_dir, hmac_key, monkeypatch):
    # Two real Plugin subclasses that register during load
    healer_src = """
from fixer_plugins import Plugin, PluginManager
class HealerP(Plugin):
    def register(self, manager: PluginManager):
        def my_healer(file_path, problem_details): return "fixed"
        manager.register_healer(my_healer)
"""
    validator_src = """
from fixer_plugins import Plugin, PluginManager
class ValidatorP(Plugin):
    def register(self, manager: PluginManager):
        def my_validator(file_path): return True
        manager.register_validator(my_validator)
"""

    healer_path = make_plugin_file(plugin_dir, "plugin_healer", healer_src)
    validator_path = make_plugin_file(plugin_dir, "plugin_validator", validator_src)

    # IMPORTANT: sign the actual file bytes (handles Windows CRLF)
    healer_sig = sha256_hex(hmac_key, healer_path.read_bytes())
    validator_sig = sha256_hex(hmac_key, validator_path.read_bytes())

    mgr = PluginManager(
        {
            "whitelisted_plugin_dirs": [str(plugin_dir)],
            "approved_plugins": {
                "plugin_healer": healer_sig,
                "plugin_validator": validator_sig,
            },
            "production_mode": True,
        }
    )

    assert not mgr.healers
    await mgr.load_plugin("plugin_healer")
    assert len(mgr.healers) == 1

    assert not mgr.validators
    await mgr.load_plugin("plugin_validator")
    assert len(mgr.validators) == 1


@pytest.mark.asyncio
async def test_load_plugin_with_signature_mismatch_raises_error(plugin_dir, hmac_key):
    src = """
from fixer_plugins import Plugin, PluginManager
class Tampered(Plugin):
    def register(self, manager: PluginManager):
        pass
"""
    p = make_plugin_file(plugin_dir, "plugin_tampered", src)
    good_sig = sha256_hex(hmac_key, src.encode("utf-8"))

    # Tamper after signing
    p.write_text(src + "\n# TAMPERED", encoding="utf-8")

    mgr = PluginManager(
        {
            "whitelisted_plugin_dirs": [str(plugin_dir)],
            "approved_plugins": {"plugin_tampered": good_sig},
            "production_mode": True,
        }
    )

    with pytest.raises(PluginValidationError, match="signature"):
        await mgr.load_plugin("plugin_tampered")


@pytest.mark.asyncio
async def test_load_plugin_from_unwhitelisted_dir_raises_noncritical(
    tmp_path, plugin_dir, hmac_key
):
    # Put plugin in one dir, but whitelist a different dir; expect NonCriticalError (not found)
    src = """
from fixer_plugins import Plugin, PluginManager
class P(Plugin):
    def register(self, manager: PluginManager): pass
"""
    make_plugin_file(plugin_dir, "plugin_outside", src)
    sig = sha256_hex(hmac_key, src.encode("utf-8"))

    other_dir = tmp_path / "other"
    other_dir.mkdir()

    mgr = PluginManager(
        {
            "whitelisted_plugin_dirs": [str(other_dir)],
            "approved_plugins": {"plugin_outside": sig},
            "production_mode": True,
        }
    )

    with pytest.raises(NonCriticalError, match="not found"):
        await mgr.load_plugin("plugin_outside")


def test_dynamic_registration_in_prod_forbidden(plugin_dir):
    mgr = PluginManager(
        {
            "whitelisted_plugin_dirs": [str(plugin_dir)],
            "production_mode": True,
        }
    )
    with pytest.raises(PluginValidationError, match="forbidden in production"):
        mgr.register_hook("x", lambda: None)
    with pytest.raises(PluginValidationError, match="forbidden in production"):
        mgr.register_healer(lambda *a, **k: None)
    with pytest.raises(PluginValidationError, match="forbidden in production"):
        mgr.register_validator(lambda *a, **k: True)
    with pytest.raises(PluginValidationError, match="forbidden in production"):
        mgr.register_diff_viewer(lambda *a, **k: None)


def test_get_plugin_signature_key_missing_in_prod_raises_error(monkeypatch):
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    monkeypatch.setattr(
        "import_fixer.fixer_plugins.SECRETS_MANAGER.get_secret",
        lambda *a, **k: None,
        raising=True,
    )
    _reset_plugin_key_for_tests()
    with pytest.raises(AnalyzerCriticalError, match="signature key not found"):
        _get_plugin_signature_key(production_mode=True)


def test_run_hook_with_exception_raises_and_alerts(monkeypatch):
    # Patch alert to inspect call
    alerted = {}

    def _alert(msg, level="INFO"):
        alerted["msg"] = msg
        alerted["level"] = level

    monkeypatch.setattr("import_fixer.fixer_plugins.alert_operator", _alert, raising=True)

    mgr = PluginManager({"stop_on_hook_error": True})  # default True

    def bad():
        raise ValueError("boom")

    mgr.register_hook("explode", bad)

    with pytest.raises(AnalyzerCriticalError):
        mgr.run_hook("explode")

    assert alerted and "explode" in alerted["msg"]
    assert alerted["level"] in ("CRITICAL", "ERROR")
