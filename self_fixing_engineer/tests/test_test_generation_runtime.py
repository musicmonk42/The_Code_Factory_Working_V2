# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# tests/test_runtime.py
import json
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

# Fix the import to be an absolute path
from test_generation.gen_agent import runtime


@pytest.fixture(autouse=True)
def reset_runtime_flags():
    """Reset dependency flags before/after tests."""
    original_flags = {k: getattr(runtime, k) for k in dir(runtime) if k.isupper()}
    yield
    for k, v in original_flags.items():
        setattr(runtime, k, v)


def test_setup_logging_no_duplicate_handlers():
    """setup_logging should not add duplicate handlers."""
    # Fix: The `setup_logging` function now correctly accepts a `level` argument.
    runtime.setup_logging(log_level=logging.DEBUG)
    count_before = len(logging.getLogger().handlers)
    runtime.setup_logging(log_level=logging.DEBUG)
    count_after = len(logging.getLogger().handlers)
    assert count_before == count_after


def test_load_config_with_env_override(tmp_path):
    """Env vars should override file config."""
    config_path = tmp_path / "config.json"
    # Fix: Use a valid JSON string for the config file.
    config_path.write_text(json.dumps({"TEST_KEY": "file_value"}))
    with patch(
        "self_fixing_engineer.test_generation.gen_agent.runtime.os.environ", {"ATCO_TEST_KEY": "env_value"}
    ):
        cfg = runtime._load_config(config_file=config_path)
        assert cfg["TEST_KEY"] == "env_value"


def test_load_config_with_dynaconf(tmp_path):
    """Should use Dynaconf if available."""
    fake_dynaconf = MagicMock()
    fake_dynaconf.Dynaconf.return_value.as_dict.return_value = {"A": 1}
    with patch.dict(sys.modules, {"dynaconf": fake_dynaconf}):
        cfg = runtime._load_config(config_file=None)
    assert cfg["A"] == 1


def test_load_config_with_pydantic(tmp_path):
    """Should use pydantic.BaseSettings if available and Dynaconf missing."""
    fake_settings = MagicMock()
    fake_settings.return_value.dict.return_value = {"B": 2}
    fake_pydantic = MagicMock()
    fake_pydantic.BaseSettings = fake_settings
    with patch.dict(sys.modules, {"dynaconf": None, "pydantic": fake_pydantic}):
        cfg = runtime._load_config()
    assert cfg["B"] == 2


def test_init_llm_pydantic():
    """
    Tests that load_config uses pydantic settings.
    This test was added as a user request.
    """
    cfg = runtime._load_config()
    assert cfg.get("B") == 2


def test_load_config_fallback():
    """Should fall back to SimpleNamespace when no libs available."""
    with patch.dict(sys.modules, {"dynaconf": None, "pydantic": None}):
        cfg = runtime._load_config()
    assert isinstance(cfg, dict)


def test_dependency_flags_set_correctly():
    """_load_and_check_deps should set *_AVAILABLE flags."""
    # Fix: The test needs to patch the existence of the modules, not just the mock.
    with patch.dict(
        "sys.modules", {"aiofiles": MagicMock(), "flask": MagicMock(), "psutil": None}
    ):
        runtime._load_and_check_deps()
        assert runtime.AIOFILES_AVAILABLE is True
        assert runtime.FLASK_AVAILABLE is True
        assert runtime.PSUTIL_AVAILABLE is False


def test_audit_logger_fallback_when_missing():
    """audit_logger should default to dummy if import fails."""
    # Fix: The test needs to patch the existence of the module `arbiter.audit_log`.
    with patch.dict(sys.modules, {"arbiter": MagicMock(), "self_fixing_engineer.arbiter.audit_log": None}):
        runtime._load_and_check_deps()
        assert hasattr(runtime.audit_logger, "log_event")


@pytest.mark.parametrize(
    "input_data,expected",
    [
        ({"password": "123"}, {"password": "[REDACTED]"}),
        ({"api_key": "abc"}, {"api_key": "[REDACTED]"}),
        ("this has api_key=12345", "this has [REDACTED]"),
    ],
)
def test_redact_sensitive(input_data, expected):
    """redact_sensitive should hide sensitive values."""
    redacted = runtime.redact_sensitive(input_data)
    assert redacted == expected


def test_runtime_import():
    """
    Tests that the core `_load_config` function can be imported and is callable.
    This serves as a guard against import chain failures.
    """
    from test_generation.gen_agent import runtime

    assert callable(runtime._load_config)
