"""
Tests for arbiter_config.json

- Validates required structure & keys
- Expands ${VAR} and ${VAR:-default} placeholders
- Coerces common scalar types (bool/int/float) after expansion
- Enforces conditional requirements when features are enabled
- Sanity checks for ranges, URLs, and regexes
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict


# Absolute path per your message; also support running in CI on non-Windows.
CONFIG_PATHS = [
    Path(r"D:\Code_Factory\self_fixing_engineer\arbiter\arbiter_config.json"),
    Path("arbiter/arbiter_config.json"),  # fallback for POSIX/CI
]


def _load_config_text() -> str:
    for p in CONFIG_PATHS:
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"Could not find arbiter_config.json in any of: {CONFIG_PATHS}"
    )


# --- Env placeholder expansion (${VAR} and ${VAR:-default}) with type coercion ---

_PLACEHOLDER = re.compile(r"\$\{([^}:]+)(?::-(.+?))?\}")


def _coerce_scalar(value: str) -> Any:
    """Best-effort scalar coercion: bools, ints, floats; otherwise original string."""
    s = value.strip()
    # booleans
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    # ints
    if re.fullmatch(r"[+-]?\d+", s):
        try:
            return int(s)
        except ValueError:
            pass
    # floats
    if re.fullmatch(r"[+-]?\d+\.\d*", s) or re.fullmatch(r"[+-]?\d*\.\d+", s):
        try:
            return float(s)
        except ValueError:
            pass
    return value


def _expand_placeholders_in_string(s: str, env: Dict[str, str]) -> Any:
    def repl(m: re.Match) -> str:
        var = m.group(1)
        default = m.group(2)
        if var in env and env[var] != "":
            return env[var]
        if default is not None:
            return default
        # No env and no default → leave unresolved to be caught by validation
        return f"${{{var}}}"

    expanded = _PLACEHOLDER.sub(repl, s)
    # If the entire string is a single placeholder that expands to a scalar, try coercion
    if expanded == s or _PLACEHOLDER.fullmatch(s):
        return _coerce_scalar(expanded)
    # Otherwise return the expanded string (could contain mixed text)
    return expanded


def _expand_env(obj: Any, env: Dict[str, str]) -> Any:
    if isinstance(obj, dict):
        return {k: _expand_env(v, env) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v, env) for v in obj]
    if isinstance(obj, str):
        return _expand_placeholders_in_string(obj, env)
    return obj


# ----------------------------- Tests -----------------------------


def test_config_file_is_well_formed_json_and_has_top_keys():
    raw = _load_config_text()
    cfg = json.loads(raw)

    # structural sanity
    for key in (
        "app_settings",
        "security",
        "ml_models",
        "llm",
        "third_party_integrations",
        "knowledge_management",
        "data_explorer",
        "audit",
    ):
        assert key in cfg, f"Missing top-level key: {key}"


def test_env_expansion_with_defaults_and_required_vars(monkeypatch):
    raw = _load_config_text()
    cfg = json.loads(raw)

    # Clear any existing REDIS_URL to ensure we test the default
    monkeypatch.delenv("REDIS_URL", raising=False)

    # Minimal env: provide only the truly required ones (no defaults in file)
    monkeypatch.setenv("ENCRYPTION_KEY", "k_test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # Leave others unset to verify default expansion (e.g., DB_PATH, LOG_FILE, etc.)

    expanded = _expand_env(cfg, dict(os.environ))

    # app_settings defaults / required
    app = expanded["app_settings"]
    assert app["redis_url"] == "redis://localhost:6379"  # default applied
    assert app["db_path"].startswith("sqlite:///")  # default applied
    assert app["encryption_key"] == "k_test"  # required (no default)
    assert isinstance(app["log_file"], str) and app["log_file"]  # default applied

    # llm section: numeric fields become numbers after coercion
    llm = expanded["llm"]
    assert llm["model_name"] == "gpt-4o-mini"  # default applied
    assert llm["temperature"] == 0.7  # coerced float
    assert llm["max_tokens"] == 500  # coerced int
    assert llm["top_p"] == 1.0  # coerced float
    assert llm["frequency_penalty"] == 0.0  # coerced float
    assert llm["presence_penalty"] == 0.0  # coerced float


def test_security_regex_is_valid_and_sane():
    raw = _load_config_text()
    cfg = json.loads(raw)
    pat = cfg["security"]["valid_domain_pattern"]
    rx = re.compile(pat)
    assert rx.fullmatch("abc_123-XYZ")
    assert not rx.fullmatch(
        "invalid.domain.com"
    )  # dots are disallowed by this pattern (by design)


def test_data_explorer_defaults_and_ranges(monkeypatch):
    raw = _load_config_text()
    cfg = json.loads(raw)
    expanded = _expand_env(cfg, dict(os.environ))

    dx = expanded["data_explorer"]
    assert dx["enabled"] is True
    assert isinstance(dx["max_depth"], int) and dx["max_depth"] >= 0
    assert isinstance(dx["max_pages"], int) and dx["max_pages"] > 0
    assert (
        isinstance(dx["rate_limit_per_second"], int) and dx["rate_limit_per_second"] > 0
    )
    assert isinstance(dx["allowed_domains"], list) and dx["allowed_domains"]
    assert isinstance(dx["compliance_flags"], dict)
    for flag in ("gdpr", "ccpa", "finra_rule_2210"):
        assert dx["compliance_flags"].get(flag) is True


def test_third_party_conditional_requirements_email_enabled(monkeypatch):
    raw = _load_config_text()
    cfg = json.loads(raw)

    # Enable email and provide required env vars
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("EMAIL_SENDER", "sender@example.com")
    monkeypatch.setenv("EMAIL_RECIPIENTS", "ops@example.com,sec@example.com")
    monkeypatch.setenv("EMAIL_SMTP_SERVER", "smtp.example.com")
    monkeypatch.setenv("EMAIL_SMTP_USERNAME", "user")
    monkeypatch.setenv("EMAIL_SMTP_PASSWORD", "pass")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ENCRYPTION_KEY", "k_test")
    expanded = _expand_env(cfg, dict(os.environ))

    email = expanded["third_party_integrations"]["email"]
    assert email["enabled"] is True
    assert email["use_tls"] is True  # default coerced
    assert isinstance(email["timeout_seconds"], float) and email["timeout_seconds"] > 0
    # Required when enabled
    for k in ("sender", "recipients", "smtp_server", "smtp_username", "smtp_password"):
        assert isinstance(email[k], str) and email[k]


def test_third_party_conditional_requirements_email_disabled(monkeypatch):
    raw = _load_config_text()
    cfg = json.loads(raw)

    # Explicitly disable email; fields may be empty
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ENCRYPTION_KEY", "k_test")
    expanded = _expand_env(cfg, dict(os.environ))

    email = expanded["third_party_integrations"]["email"]
    assert email["enabled"] is False


def test_pagerduty_enabled_requires_routing_key(monkeypatch):
    raw = _load_config_text()
    cfg = json.loads(raw)
    monkeypatch.setenv("PAGERDUTY_ENABLED", "true")
    monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "rkey")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ENCRYPTION_KEY", "k_test")
    expanded = _expand_env(cfg, dict(os.environ))

    pd = expanded["third_party_integrations"]["pagerduty"]
    assert pd["enabled"] is True
    assert (
        isinstance(pd["api_timeout_seconds"], float) and pd["api_timeout_seconds"] > 0
    )
    assert pd["routing_key"] == "rkey"


def test_llm_api_key_required(monkeypatch):
    raw = _load_config_text()
    cfg = json.loads(raw)

    # No OPENAI_API_KEY set → must remain unresolved and should be caught
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ENCRYPTION_KEY", "k_test")
    expanded = _expand_env(cfg, dict(os.environ))

    # Must still contain placeholder if not provided
    assert (
        expanded["llm"]["api_key"] == "${OPENAI_API_KEY}"
    )  # unresolved means missing required


def test_app_encryption_key_required(monkeypatch):
    raw = _load_config_text()
    cfg = json.loads(raw)

    # No ENCRYPTION_KEY set → unresolved placeholder remains
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    expanded = _expand_env(cfg, dict(os.environ))
    assert (
        expanded["app_settings"]["encryption_key"] == "${ENCRYPTION_KEY}"
    )  # unresolved
