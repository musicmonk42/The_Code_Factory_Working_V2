# tests/test_config.py
"""Production-grade tests for config.py.
Covers path safety, deep-merge behavior, immutability, defaults integrity,
artifact directory creation, and robustness to malformed inputs and symlinks."""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
from types import MappingProxyType
from typing import Mapping

import pytest

# Ensure module path (adjust if needed for your runner)
MODULE_ROOT = pathlib.Path(os.environ.get("ATCO_MODULE_ROOT", "/mnt/data"))
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

import importlib

config = importlib.import_module("self_fixing_engineer.test_generation.orchestrator.config")


# -----------------------------
# Helpers / fixtures
# -----------------------------
@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    yield


@pytest.fixture
def project(tmp_path: pathlib.Path):
    root = tmp_path / "repo"
    root.mkdir()
    # Expected artifact root base
    (root / "atco_artifacts").mkdir()
    return root


def read_json(path: pathlib.Path) -> Mapping:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# -----------------------------
# load_config – correctness & safety
# -----------------------------
class TestLoadConfig:
    def test_missing_file_uses_defaults_and_sets_globals(self, project: pathlib.Path):
        frozen = config.load_config(str(project), "atco_config.json")
        assert isinstance(frozen, Mapping)
        assert (
            frozen["max_parallel_generation"]
            == config.DEFAULT_CONFIG["max_parallel_generation"]
        )
        assert config.PROJECT_ROOT == str(project.resolve())
        assert isinstance(frozen, MappingProxyType)

    def test_invalid_json_falls_back_with_warning(
        self, project: pathlib.Path, caplog: pytest.LogCaptureFixture
    ):
        cfg_path = project / "atco_config.json"
        cfg_path.write_text('{"bad": 1}', encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            frozen = config.load_config(str(project), str(cfg_path))
        assert (
            frozen["backend_timeouts"]["pynguin"]
            == config.DEFAULT_CONFIG["backend_timeouts"]["pynguin"]
        )
        assert "validation error(s)" in caplog.text

    def test_top_level_not_object_is_rejected_but_defaults_used(
        self, project: pathlib.Path, caplog: pytest.LogCaptureFixture
    ):
        cfg_path = project / "atco_config.json"
        cfg_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            frozen = config.load_config(str(project), str(cfg_path))
        assert frozen["source_dirs"]["python"] == tuple(
            config.DEFAULT_CONFIG["source_dirs"]["python"]
        )  # lists frozen to tuple
        assert "Top-level config must be a JSON object" in caplog.text

    def test_path_traversal_is_blocked(self, project: pathlib.Path):
        with pytest.raises(ValueError):
            config.load_config(str(project), os.path.join("..", "evil.json"))

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Creating symlinks requires special privileges on Windows",
    )
    def test_symlink_escaping_is_blocked(
        self, project: pathlib.Path, tmp_path: pathlib.Path
    ):
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        sneaky = project / "link.json"
        sneaky.symlink_to(outside)  # realpath escapes project root
        with pytest.raises(ValueError):
            config.load_config(str(project), sneaky.name)

    def test_deep_merge_and_defaults_unchanged(self, project: pathlib.Path):
        cfg_path = project / "atco_config.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "max_parallel_generation": 9,
                    "backend_timeouts": {"pynguin": 120},
                    "new_key": "x",  # This key is unknown and should be ignored after validation
                }
            ),
            encoding="utf-8",
        )
        before_snapshot = json.dumps(config.DEFAULT_CONFIG, sort_keys=True)
        frozen = config.load_config(str(project), str(cfg_path))
        assert frozen["max_parallel_generation"] == 9
        assert frozen["backend_timeouts"]["pynguin"] == 120
        assert frozen["retry_backoff_min"] == config.DEFAULT_CONFIG["retry_backoff_min"]
        after_snapshot = json.dumps(config.DEFAULT_CONFIG, sort_keys=True)
        assert before_snapshot == after_snapshot  # no mutation to defaults

    def test_immutability_and_independence_from_globals(self, project: pathlib.Path):
        cfg_path = project / "atco_config.json"
        cfg_path.write_text(
            json.dumps({"jira_integration": {"enabled": True}}), encoding="utf-8"
        )
        frozen = config.load_config(str(project), str(cfg_path))
        with pytest.raises(TypeError):
            frozen["new"] = 1  # type: ignore[index]
        with pytest.raises(TypeError):
            frozen["jira_integration"]["enabled"] = False  # type: ignore[index]
        assert isinstance(frozen["source_dirs"]["python"], tuple)
        # mutate CONFIG after the fact; frozen should not reflect changes
        config.CONFIG["jira_integration"]["enabled"] = False
        assert frozen["jira_integration"]["enabled"] is True

    def test_coerce_critical_dicts_when_wrong_types(
        self, project: pathlib.Path, caplog
    ):
        cfg_path = project / "atco_config.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "backend_timeouts": [1, 2],
                    "source_dirs": "oops",
                }
            ),
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING):
            frozen = config.load_config(str(project), str(cfg_path))

        assert isinstance(frozen["backend_timeouts"], Mapping)
        assert isinstance(frozen["source_dirs"], Mapping)
        # Assert that a warning was logged due to the type mismatch
        assert "Configuration merge conflict" in caplog.text


# -----------------------------
# _ensure_artifact_dirs – filesystem side effects
# -----------------------------
class TestEnsureArtifactDirs:
    def test_creates_all_directories_and_audit_parent(self, project: pathlib.Path):
        cfg = {
            "generated_output_dir": "custom/gen",
            "quarantine_dir": "custom/quarantine",
            "sarif_export_dir": "custom/sarif",
            "coverage_reports_dir": "custom/coverage",
            "html_reports_dir": "custom/html",
            "venv_temp_dir": "custom/venv",
            "audit_log_file": "custom/logs/audit.jsonl",
        }
        config._ensure_artifact_dirs(str(project), cfg)
        for key, rel in cfg.items():
            if key.endswith("_dir"):
                assert (project / rel).is_dir()
        assert (project / "custom" / "logs").is_dir()

    def test_uses_defaults_when_not_overridden(self, project: pathlib.Path):
        # Calling load_config implicitly triggers _ensure_artifact_dirs
        config.load_config(str(project), "nonexistent.json")
        assert (project / config.GENERATED_OUTPUT_DIR).is_dir()
        assert (project / config.QUARANTINE_DIR).is_dir()
        assert (project / config.SARIF_EXPORT_DIR).is_dir()
        assert (project / config.COVERAGE_REPORTS_DIR).is_dir()
        assert (project / pathlib.Path(config.AUDIT_LOG_FILE).parent).is_dir()


# -----------------------------
# Regression guards
# -----------------------------
class TestRegressionGuards:
    def test_logging_config_is_json_serializable(self):
        # why: logging config may be emitted/templated in infra
        json.dumps(config.LOGGING_CONFIG)

    def test_default_config_json_roundtrip(self):
        s = json.dumps(config.DEFAULT_CONFIG)
        roundtrip = json.loads(s)
        assert (
            roundtrip["max_parallel_generation"]
            == config.DEFAULT_CONFIG["max_parallel_generation"]
        )


# -----------------------------
# Property-style fuzzing (optional, cheap)
# -----------------------------
@pytest.mark.parametrize("bad_value", [None, 0, 1.23, "x", [1, 2, 3]])
def test_deep_merge_resilience_on_malformed_overrides(
    project: pathlib.Path, bad_value, caplog
):
    cfg_path = project / "atco_config.json"
    # Place malformed types under keys that expect dict/list
    cfg_path.write_text(
        json.dumps(
            {
                "backend_timeouts": bad_value,
                "source_dirs": bad_value,
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        frozen = config.load_config(str(project), str(cfg_path))

    assert isinstance(frozen["backend_timeouts"], Mapping)
    assert isinstance(frozen["source_dirs"], Mapping)
    # Assert a warning was issued for the type mismatch
    assert "Configuration merge conflict" in caplog.text


# -----------------------------
# Smoke: _ensure_dir normalization
# -----------------------------
def test_ensure_dir_normalizes_and_creates(project: pathlib.Path):
    rel = os.path.join(".", "a", "..", "b", "c")
    full = config._ensure_dir(str(project), rel)
    assert pathlib.Path(full).is_dir()
    assert pathlib.Path(full).resolve().is_relative_to(project.resolve())


# Add test:
def test_config_load():
    cfg = config.load_config(".", "config.json")
    assert cfg["max_parallel_generation"] == 4
