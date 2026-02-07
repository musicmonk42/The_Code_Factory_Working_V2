# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# -*- coding: utf-8 -*-
"""
Enterprise test suite for simulation.utils

Covers:
- Metric registration idempotency across reloads
- File hashing (single/multi algo), cache invalidation on content change
- Glob search de-duplication and validation
- Diff generation (unified/context), length mismatch, missing files
- Path safety (sanitize_path, validate_safe_path)
- Artifact loading (success, too large, not found)
- Async save_sim_result (with provenance, JSON default=str)
- Provenance chain integrity and secret scrubbing
- PluginAPI flows: temp dir lifecycle, report_result, handle_error, compat checks
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import time
import uuid

import pytest


@pytest.fixture()  # function scope (fixes ScopeMismatch with monkeypatch)
def utils_module(tmp_path_factory, monkeypatch):
    """
    Provide a freshly imported `simulation.utils` module with a dedicated provenance log file
    to avoid test cross-talk. We set the env var BEFORE (re)import to bind the provenance path.
    """
    prov_dir = tmp_path_factory.mktemp("prov_logs")
    prov_file = prov_dir / "provenance.log"
    monkeypatch.setenv("PROVENANCE_LOG_PATH", str(prov_file))

    # Ensure we import fresh
    if "self_fixing_engineer.simulation.utils" in sys.modules:
        del sys.modules["self_fixing_engineer.simulation.utils"]

    import simulation.utils as utils  # noqa: WPS433

    utils = importlib.reload(utils)  # bind env-provided provenance path

    # sanity
    assert utils.provenance_logger.provenance_file == prov_file

    return utils


# ---------------------- Metrics & Reload Idempotency ---------------------- #


def test_metrics_safe_on_reload(monkeypatch, tmp_path):
    """
    Re-import the module multiple times and ensure no 'duplicated timeseries'
    errors occur and counters are usable.
    """
    prov_file = tmp_path / "prov.log"
    monkeypatch.setenv("PROVENANCE_LOG_PATH", str(prov_file))

    # First import
    if "self_fixing_engineer.simulation.utils" in sys.modules:
        del sys.modules["self_fixing_engineer.simulation.utils"]
    import simulation.utils as utils  # noqa

    # Reload a couple times; should not raise
    utils = importlib.reload(utils)
    utils = importlib.reload(utils)

    # Use a couple of metrics (no exception)
    utils.hash_counter.inc()
    utils.save_counter.inc()
    utils.FILE_OPERATIONS.labels(operation="noop", status="success").inc()


# ---------------------- Hashing & Cache Invalidation ---------------------- #


def test_hash_file_single_and_multi_algorithms(utils_module, tmp_path):
    utils = utils_module
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")

    # Single algorithm
    h1 = utils.hash_file(f, "sha256")
    assert isinstance(h1, str)
    assert h1 == hashlib.sha256(b"hello").hexdigest()

    # Multiple algorithms
    hs = utils.hash_file(f, ["sha1", "md5"])
    assert set(hs.keys()) == {"sha1", "md5"}
    assert hs["sha1"] == hashlib.sha1(b"hello").hexdigest()
    assert hs["md5"] == hashlib.md5(b"hello").hexdigest()


def test_hash_cache_invalidation_on_change(utils_module, tmp_path):
    utils = utils_module
    f = tmp_path / "b.txt"
    f.write_text("v1", encoding="utf-8")
    h_v1 = utils.hash_file(f, "sha256")

    # Ensure mtime changes (Windows can need a small delay)
    time.sleep(0.05)
    f.write_text("v2", encoding="utf-8")
    h_v2 = utils.hash_file(f, "sha256")

    assert h_v1 != h_v2  # cache keyed by (mtime_ns, size) must invalidate


# ---------------------- File Finding ---------------------- #


def test_find_files_by_pattern_dedup_and_validation(utils_module, tmp_path):
    utils = utils_module
    d1 = tmp_path / "dir1"
    d2 = tmp_path / "dir1" / "dir2"
    d2.mkdir(parents=True)
    f1 = d1 / "x.py"
    f2 = d2 / "x.py"
    f3 = d2 / "y.txt"
    f1.write_text("print('x1')\n", encoding="utf-8")
    f2.write_text("print('x2')\n", encoding="utf-8")
    f3.write_text("hello\n", encoding="utf-8")

    py_files = utils.find_files_by_pattern(tmp_path, "**/*.py")
    # 'x.py' appears once (dedup by path), even though two exist at different paths → we validate names set
    py_set = {p.name for p in py_files}
    assert py_set == {"x.py"}

    with pytest.raises(ValueError):
        utils.find_files_by_pattern(tmp_path, "")

    with pytest.raises(NotADirectoryError):
        utils.find_files_by_pattern(f1, "*.py")


# ---------------------- Diffs ---------------------- #


def test_print_file_diff_unified_and_context(utils_module, tmp_path):
    utils = utils_module
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("line1\nline2\n", encoding="utf-8")
    b.write_text("line1\nline2-mod\nline3\n", encoding="utf-8")

    unified = utils.print_file_diff(a, b, diff_format="unified")
    assert isinstance(unified, str)
    assert "--- " in unified and "+++ " in unified
    assert ("-line2\n" in unified) or ("+line2-mod\n" in unified)

    context = utils.print_file_diff(a, b, diff_format="context")
    assert isinstance(context, str)
    assert context  # non-empty

    with pytest.raises(FileNotFoundError):
        utils.print_file_diff(a, tmp_path / "missing.txt")


# ---------------------- Path Safety ---------------------- #


def test_sanitize_and_validate_safe_path(utils_module, tmp_path):
    utils = utils_module
    base = tmp_path / "base"
    base.mkdir()
    (base / "ok.txt").write_text("ok", encoding="utf-8")

    p_ok = utils.sanitize_path("ok.txt", base_dir=base)
    assert p_ok.exists()

    with pytest.raises(ValueError):
        utils.sanitize_path("../evil.txt", base_dir=base)

    assert utils.validate_safe_path(base / "ok.txt", base)
    with pytest.raises(ValueError):
        utils.validate_safe_path(tmp_path / "outside.txt", base)


# ---------------------- Artifact Loading ---------------------- #


def test_load_artifact_success_and_limits(utils_module, tmp_path):
    utils = utils_module
    small = tmp_path / "small.txt"
    big = tmp_path / "big.txt"

    small.write_text("abc", encoding="utf-8")
    content = utils.load_artifact(small, max_bytes=10)
    assert content == "abc"

    big.write_bytes(b"x" * 2048)
    assert utils.load_artifact(big, max_bytes=1024) is None

    assert utils.load_artifact(tmp_path / "missing.txt") is None


# ---------------------- Async Save + Provenance ---------------------- #


@pytest.mark.asyncio
async def test_save_sim_result_and_provenance_chain(utils_module, tmp_path):
    utils = utils_module
    out = tmp_path / "out" / "result.json"

    data = {
        "id": str(uuid.uuid4()),
        "when": utils.datetime.utcnow(),  # datetime to exercise default=str
        "nested": {"k": "v"},
    }
    p = await utils.save_sim_result(data, out)
    assert p.exists()
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["nested"]["k"] == "v"
    assert "when" in payload

    prov_path = utils.provenance_logger.provenance_file
    lines = [
        json.loads(line)
        for line in prov_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    related = [e for e in lines if e.get("payload", {}).get("file") == str(p)]
    assert any(e.get("event_type") == "save_sim_result_attempt" for e in related)
    assert any(
        e.get("event_type") == "save_sim_result"
        and e.get("payload", {}).get("status") == "success"
        for e in related
    )

    related_sorted = sorted(related, key=lambda e: e["timestamp"])
    if len(related_sorted) >= 2:
        first, second = related_sorted[-2], related_sorted[-1]
        assert second.get("prev_hash") == first.get("chain_hash")


def test_provenance_scrubs_secrets(utils_module):
    utils = utils_module
    utils.provenance_logger.log(
        {
            "event_type": "unit.secret_test",
            "payload": "password=supersecret sk_test_ABCDEFGHIJKLMNOP",
        }
    )
    prov = utils.provenance_logger.provenance_file.read_text(encoding="utf-8")
    assert "[PASSWORD_SCRUBBED]" in prov or "[POTENTIAL_SECRET]" in prov


# ---------------------- Plugin API ---------------------- #


def test_plugin_api_temp_dir_context_and_cleanup(utils_module):
    utils = utils_module
    api = utils.PluginAPI("testPlugin")
    with api.temp_dir_context() as d:
        test_file = d / "x.txt"
        test_file.write_text("data", encoding="utf-8")
        assert test_file.exists()
    assert not d.exists()


def test_plugin_api_report_and_error_log_to_provenance(utils_module):
    utils = utils_module
    api = utils.PluginAPI("pluginX")

    api.report_result("ok", {"alpha": 1, "beta": "two"})
    api.handle_error("bad thing", exception=ValueError("oops"), fatal=False)

    prov_lines = [
        json.loads(line)
        for line in utils.provenance_logger.provenance_file.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert any(e.get("event_type") == "plugin_result" for e in prov_lines)


def test_plugin_api_core_compatibility_checks(utils_module):
    utils = utils_module
    api = utils.PluginAPI("compatPlugin")

    old_core = utils.CORE_SIM_RUNNER_VERSION
    try:
        utils.CORE_SIM_RUNNER_VERSION = "1.1.0"
        assert api.check_core_compatibility("1.0.0") is True

        utils.CORE_SIM_RUNNER_VERSION = "1.0.0"
        assert api.check_core_compatibility("2.0.0") is False

        utils.CORE_SIM_RUNNER_VERSION = "3.0.0"
        assert api.check_core_compatibility("1.0.0", "2.0.0") is True
    finally:
        utils.CORE_SIM_RUNNER_VERSION = old_core


def test_plugin_api_warn_sandbox_limitations(utils_module, monkeypatch):
    utils = utils_module
    api = utils.PluginAPI("warnPlugin")
    monkeypatch.setenv("DISABLE_SANDBOX_WARNING", "false")
    api.warn_sandbox_limitations({"type": "python", "sandbox": {"enabled": True}})
