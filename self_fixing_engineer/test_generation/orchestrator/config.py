import os
import json
import io
import copy
import unittest
import tempfile
from typing import Dict, Any
from types import MappingProxyType
import logging
import logging.config
import sys
from jsonschema import SchemaError, Draft7Validator

# --- ATCO Artifacts Directories (Relative to PROJECT_ROOT) ---
# Hardcoded paths have been moved into DEFAULT_CONFIG for externalization
# This makes them configurable via atco_config.json

# Refactored to be passed explicitly, but initialized here for global access
CONFIG: Dict[str, Any] = {}
PROJECT_ROOT: str = "."

# This configuration is loaded as a default and can be overridden by atco_config.json
DEFAULT_CONFIG: Dict[str, Any] = {
    # --- Artifact Directories ---
    "quarantine_dir": "atco_artifacts/quarantined_tests",
    "generated_output_dir": "atco_artifacts/generated",
    "sarif_export_dir": "atco_artifacts/sarif_reports",
    "audit_log_file": "atco_artifacts/atco_audit.log",
    "coverage_reports_dir": "atco_artifacts/coverage_reports",
    "html_reports_dir": "atco_artifacts/html_reports",
    "venv_temp_dir": "atco_artifacts/venv_temp",
    # --- Core Runtime Settings ---
    "max_parallel_generation": 4,
    "max_gen_retries": 2,
    "retry_backoff_min": 1,
    "retry_backoff_max": 10,
    "venv_install_timeout_seconds": 180,
    "test_exec_timeout_seconds": 30,
    "backend_timeouts": {"pynguin": 60, "jest_llm": 90, "diffblue": 180},
    "source_dirs": {
        "python": ["src", "app"],
        "javascript": ["src/js", "src/ts"],
        "java": ["src/main/java"],
        "rust": ["src"],
        "go": ["src"],
    },
    "prioritization_rules": {"language_boosts": {"python": 5, "java": 7}},
    "abort_on_critical": False,
    "is_demo_mode": False,
    "per_lang_concurrency": 4,
    "demo_per_lang_concurrency": 2,
    "compliance_reporting": {"enabled": False},
    # --- Integrations ---
    "jira_integration": {"enabled": False, "api_url": "", "project_key": "ATCO"},
    "pr_integration": {"enabled": False},
    "slack_webhook_url": None,
    "slack_events": ["test_quarantined", "test_requires_pr", "security_alert"],
    "webhook_hooks": {},
    "webhook_events": ["atco_run_finished"],
    # --- Policies & Scans ---
    "policy_config_path": "atco_policies.json",
    "security_scan_threshold": "NONE",
    "enrichment_plugins": {
        "header_enabled": True,
        "mocking_import_enabled": True,
        "llm_refinement_enabled": False,
    },
    "security_scan_sim_critical_issue_chance": 0.01,
    "mutation_testing": {"enabled": False, "min_score_for_integration": 50.0},
    # --- Environment & Backends ---
    "python_venv_deps": ["pytest", "pytest-cov"],
    "test_generation_backends": {
        "python": {
            "module": "test_generation.backends",
            "class": "PynguinBackend",
            "isolation_strategy": "virtualenv",
            "required_deps": ["pytest", "pytest-cov"],
        },
        "javascript": {
            "module": "test_generation.backends",
            "class": "JestLLMBackend",
            "isolation_strategy": "default",
            "required_deps": ["jest", "ts-jest"],
        },
        "typescript": {
            "module": "test_generation.backends",
            "class": "JestLLMBackend",
            "isolation_strategy": "default",
            "required_deps": ["jest", "ts-jest"],
        },
        "java": {
            "module": "test_generation.backends",
            "class": "DiffblueBackend",
            "isolation_strategy": "default",
            "required_deps": [],
        },
        "rust": {
            "module": "test_generation.backends",
            "class": "CargoBackend",
            "isolation_strategy": "default",
            "required_deps": [],
        },
        "go": {
            "module": "test_generation.backends",
            "class": "GoBackend",
            "isolation_strategy": "default",
            "required_deps": [],
        },
    },
    # --- Console and Logging ---
    "console_glyphs": {"check": "✓", "warn": "▲", "x": "✗"},
    "console_ascii_glyphs": {"check": "[OK]", "warn": "[!]", "x": "[X]"},
    "log_styles": {
        "INFO": {"console_style": "default"},
        "SUCCESS": {"console_style": "bold green"},
        "WARNING": {"console_style": "yellow"},
        "ERROR": {"console_style": "red"},
        "CRITICAL": {"console_style": "bold white on red"},
    },
}

QUARANTINE_DIR = DEFAULT_CONFIG.get("quarantine_dir", "atco_artifacts/quarantined_tests")
GENERATED_OUTPUT_DIR = DEFAULT_CONFIG.get("generated_output_dir", "atco_artifacts/generated")
SARIF_EXPORT_DIR = DEFAULT_CONFIG.get("sarif_export_dir", "atco_artifacts/sarif_reports")
AUDIT_LOG_FILE = DEFAULT_CONFIG.get("audit_log_file", "atco_artifacts/atco_audit.log")
COVERAGE_REPORTS_DIR = DEFAULT_CONFIG.get("coverage_reports_dir", "atco_artifacts/coverage_reports")
HTML_REPORTS_DIR = DEFAULT_CONFIG.get("html_reports_dir", "atco_artifacts/html_reports")
VENV_TEMP_DIR = DEFAULT_CONFIG.get("venv_temp_dir", "atco_artifacts/venv_temp")


# Define the complete LOGGING_CONFIG dictionary
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        },
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "filename": "atco.log",
            "maxBytes": 1024 * 1024 * 5,
            "backupCount": 5,
        },
        "audit_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filename": DEFAULT_CONFIG["audit_log_file"],
            "maxBytes": 1024 * 1024 * 10,
            "backupCount": 2,
        },
    },
    "loggers": {
        "": {"handlers": ["console", "file"], "level": "INFO", "propagate": True},
        "atco_audit": {"handlers": ["audit_file"], "level": "INFO", "propagate": False},
    },
}

# Schema for validation
CONFIG_SCHEMA = {
    "type": "object",
    "properties": {
        "max_parallel_generation": {"type": "integer", "minimum": 1},
        "max_gen_retries": {"type": "integer", "minimum": 0},
        "retry_backoff_min": {"type": "number", "minimum": 0},
        "retry_backoff_max": {"type": "number", "minimum": 0},
        "venv_install_timeout_seconds": {"type": "integer", "minimum": 1},
        "test_exec_timeout_seconds": {"type": "integer", "minimum": 1},
        "per_lang_concurrency": {"type": "integer", "minimum": 1},
        "demo_per_lang_concurrency": {"type": "integer", "minimum": 1},
        "is_demo_mode": {"type": "boolean"},
        "abort_on_critical": {"type": "boolean"},
        "compliance_reporting": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "additionalProperties": False,
        },
        "backend_timeouts": {
            "type": "object",
            "patternProperties": {".*": {"type": "number", "minimum": 1}},
        },
        "source_dirs": {
            "type": "object",
            "patternProperties": {".*": {"type": "array", "items": {"type": "string"}}},
        },
        "prioritization_rules": {
            "type": "object",
            "properties": {
                "language_boosts": {
                    "type": "object",
                    "patternProperties": {".*": {"type": "number", "minimum": 0}},
                    "additionalProperties": False,
                }
            },
            "additionalProperties": False,
        },
        "jira_integration": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "api_url": {"type": "string"},
                "project_key": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "pr_integration": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "additionalProperties": False,
        },
        "slack_webhook_url": {"type": ["string", "null"]},
        "slack_events": {"type": "array", "items": {"type": "string"}},
        "webhook_hooks": {"type": "object"},
        "webhook_events": {"type": "array", "items": {"type": "string"}},
        "policy_config_path": {"type": "string"},
        "security_scan_threshold": {"type": "string"},
        "enrichment_plugins": {
            "type": "object",
            "properties": {
                "header_enabled": {"type": "boolean"},
                "mocking_import_enabled": {"type": "boolean"},
                "llm_refinement_enabled": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "security_scan_sim_critical_issue_chance": {"type": "number"},
        "mutation_testing": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "min_score_for_integration": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            "additionalProperties": False,
        },
        "python_venv_deps": {"type": "array", "items": {"type": "string"}},
        "test_generation_backends": {
            "type": "object",
            "patternProperties": {
                ".*": {
                    "type": "object",
                    "properties": {
                        "module": {"type": "string"},
                        "class": {"type": "string"},
                        "isolation_strategy": {"type": "string"},
                        "required_deps": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["module", "class"],
                    "additionalProperties": False,
                }
            },
        },
        "quarantine_dir": {"type": "string"},
        "generated_output_dir": {"type": "string"},
        "sarif_export_dir": {"type": "string"},
        "audit_log_file": {"type": "string"},
        "coverage_reports_dir": {"type": "string"},
        "html_reports_dir": {"type": "string"},
        "venv_temp_dir": {"type": "string"},
        "console_glyphs": {
            "type": "object",
            "properties": {
                "check": {"type": "string"},
                "warn": {"type": "string"},
                "x": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "console_ascii_glyphs": {
            "type": "object",
            "properties": {
                "check": {"type": "string"},
                "warn": {"type": "string"},
                "x": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "log_styles": {
            "type": "object",
            "patternProperties": {
                ".*": {
                    "type": "object",
                    "properties": {"console_style": {"type": "string"}},
                    "required": ["console_style"],
                }
            },
        },
    },
    "additionalProperties": False,
}

__all__ = [
    "CONFIG",
    "PROJECT_ROOT",
    "LOGGING_CONFIG",
    "QUARANTINE_DIR",
    "GENERATED_OUTPUT_DIR",
    "SARIF_EXPORT_DIR",
    "AUDIT_LOG_FILE",
    "COVERAGE_REPORTS_DIR",
    "VENV_TEMP_DIR",
    "HTML_REPORTS_DIR",
    "_ensure_dir",
    "_ensure_artifact_dirs",
    "load_config",
]


def sanitize_path(base: str, rel: str) -> str:
    """Joins a base path and a relative path, ensuring the result is within the base."""
    abs_root = os.path.realpath(os.path.abspath(base))
    abs_path = os.path.realpath(os.path.abspath(os.path.join(abs_root, rel)))
    if not abs_path.startswith(abs_root + os.sep) and abs_path != abs_root:
        raise ValueError(f"Path '{rel}' attempts to traverse outside of project root '{base}'.")
    return abs_path


def _mkdir_parent_first(path: str) -> None:
    """
    Create `path` reliably even if os.path.exists is monkey-patched.
    Strategy: create parent with makedirs (non-recursive needed),
    then create the leaf with os.mkdir; ignore EEXIST; fallback to makedirs.
    """
    parent = os.path.dirname(path.rstrip("\\/"))
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)  # parent of leaf; project_root exists
        except Exception:
            # Non-fatal; we'll still attempt the leaf
            pass
    try:
        os.mkdir(path)
    except FileExistsError:
        return
    except FileNotFoundError:
        # Parent wasn’t actually created due to monkey-patching; last-resort create full tree
        os.makedirs(path, exist_ok=True)


def _ensure_dir(root: str, rel: str) -> str:
    full = os.path.normpath(os.path.join(root, rel))
    os.makedirs(full, exist_ok=True)
    return full


def _ensure_artifact_dirs(project_root: str, config: dict) -> None:
    """
    Ensures all configured artifact directories exist using a robust method.
    """
    artifact_dirs = [
        config.get("generated_output_dir"),
        config.get("quarantine_dir"),
        config.get("sarif_export_dir"),
        config.get("coverage_reports_dir"),
        config.get("html_reports_dir"),
        config.get("venv_temp_dir"),
    ]
    for rel in artifact_dirs:
        if rel:
            # FIX: Correct argument order for sanitize_path
            full = sanitize_path(project_root, rel)
            _mkdir_parent_first(full)

    audit_log_rel = config.get("audit_log_file")
    if audit_log_rel:
        # FIX: Correct argument order for sanitize_path
        audit_full = sanitize_path(project_root, audit_log_rel)
        _mkdir_parent_first(os.path.dirname(audit_full))


def _deep_freeze(obj: Any) -> Any:
    if isinstance(obj, dict):
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    elif isinstance(obj, list):
        return tuple(_deep_freeze(item) for item in obj)
    else:
        return obj


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any], parent_key: str = "") -> None:
    for k, v_src in src.items():
        v_dst = dst.get(k)
        if isinstance(v_dst, dict) and isinstance(v_src, dict):
            _deep_merge(v_dst, v_src, parent_key=f"{parent_key}.{k}" if parent_key else k)
        elif isinstance(v_dst, dict) and not isinstance(v_src, dict):
            logging.warning(
                f"Configuration merge conflict: Cannot merge non-dictionary value '{v_src}' "
                f"for key '{k}' into an existing dictionary. Keeping the default dictionary value."
            )
        else:
            dst[k] = v_src


def load_config(project_root: str, config_file: str) -> MappingProxyType:
    def _sanitize_path(base: str, rel: str) -> str:
        abs_root = os.path.realpath(os.path.abspath(base))
        abs_path = os.path.realpath(os.path.abspath(os.path.join(abs_root, rel)))
        if not abs_path.startswith(abs_root + os.sep) and abs_path != abs_root:
            raise ValueError(f"Config path '{rel}' is outside project root.")
        return abs_path

    def _safe_join(base: str, rel: str) -> str:
        # Prevent absolute paths and traversal outside base
        root = os.path.realpath(os.path.abspath(base))
        # Normalize rel relative to root (absolute rel will be treated as absolute)
        candidate = os.path.realpath(os.path.abspath(os.path.join(root, rel)))
        if not candidate.startswith(root + os.sep):
            raise ValueError(f"Config value path '{rel}' is outside project root.")
        return candidate

    global PROJECT_ROOT
    PROJECT_ROOT = os.path.abspath(project_root)

    # Critical errors like path traversal should fail fast.
    full_config_path = _sanitize_path(PROJECT_ROOT, config_file)

    merged_config: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)

    user_config_was_loaded = False

    # Gracefully handle recoverable errors: warn and fall back to defaults.
    try:
        if os.path.exists(full_config_path):
            with io.open(full_config_path, "r", encoding="utf-8") as f:
                user_config = json.load(f)
            if not isinstance(user_config, dict):
                raise ValueError("Top-level config must be a JSON object.")

            _deep_merge(merged_config, user_config)
            user_config_was_loaded = True
    except (ValueError, json.JSONDecodeError) as e:
        logging.warning(
            f"Failed to load or parse '{config_file}'. Using full default configuration. Error: {e}"
        )
        merged_config = copy.deepcopy(DEFAULT_CONFIG)
        user_config_was_loaded = False

    # If we loaded a user config, perform targeted validation and coercion.
    if user_config_was_loaded:
        try:
            validator = Draft7Validator(CONFIG_SCHEMA)
            errors = sorted(validator.iter_errors(merged_config), key=lambda e: str(e.path))

            if errors:
                logging.warning(
                    f"Found {len(errors)} validation error(s) in configuration. Reverting invalid fields to their defaults."
                )
                # 1) Log each error with friendly wording
                for err in errors:
                    path_elems = list(err.path)
                    field = str(path_elems[-1]) if path_elems else "<root>"
                    if err.validator == "type":
                        expected = err.schema.get("type")
                        got = type(err.instance).__name__
                        logging.warning(f"'{field}' should be of type '{expected}' (got {got}).")
                    elif err.validator == "additionalProperties":
                        # Try to extract unexpected keys
                        unexpected = None
                        if isinstance(err.instance, dict):
                            allowed = set((err.schema.get("properties") or {}).keys())
                            unexpected = sorted(k for k in err.instance.keys() if k not in allowed)
                        if unexpected:
                            logging.warning(
                                f"Unexpected config keys at '{'.'.join(map(str, err.path)) or '<root>'}': {unexpected}"
                            )
                        else:
                            logging.warning(
                                f"Validation error at '{'.'.join(map(str, err.path)) or '<root>'}': {err.message}"
                            )
                    else:
                        logging.warning(
                            f"Validation error at '{'.'.join(map(str, err.path)) or '<root>'}': {err.message}"
                        )

                # 2) Revert top-level fields implicated by errors
                keys_to_revert = set()
                for err in errors:
                    if err.path:
                        keys_to_revert.add(err.path[0])

                # 3) Also drop unknown top-level keys (additionalProperties on root)
                schema_top_keys = set(CONFIG_SCHEMA["properties"].keys())
                for k in list(merged_config.keys()):
                    if k not in schema_top_keys:
                        logging.warning(f"Removing unknown top-level key '{k}' from configuration.")
                        del merged_config[k]

                for key in keys_to_revert:
                    if key in DEFAULT_CONFIG:
                        merged_config[key] = copy.deepcopy(DEFAULT_CONFIG[key])
                    elif key in merged_config:
                        del merged_config[key]

        except SchemaError as e:
            raise RuntimeError(f"Internal error: Configuration schema is invalid: {e}") from e

    # Anchor audit log path to project root
    LOGGING_CONFIG["handlers"]["audit_file"]["filename"] = os.path.join(
        PROJECT_ROOT,
        merged_config.get("audit_log_file", DEFAULT_CONFIG["audit_log_file"]),
    )

    # (Optional) also anchor the regular file handler to project root
    if "file" in LOGGING_CONFIG["handlers"]:
        LOGGING_CONFIG["handlers"]["file"]["filename"] = os.path.join(PROJECT_ROOT, "atco.log")

    CONFIG.clear()
    CONFIG.update(merged_config)

    # Create artifact dirs safely (guard against traversal/absolute paths)
    def _ensure_artifact_dirs_safe(project_root: str, cfg: Dict[str, Any]) -> None:
        for key in (
            "generated_output_dir",
            "quarantine_dir",
            "sarif_export_dir",
            "coverage_reports_dir",
            "html_reports_dir",
            "venv_temp_dir",
        ):
            rel = cfg.get(key)
            if rel:
                full = _safe_join(project_root, rel)
                os.makedirs(full, exist_ok=True)
        audit_rel = cfg.get("audit_log_file")
        if audit_rel:
            audit_full = _safe_join(project_root, audit_rel)
            os.makedirs(os.path.dirname(audit_full), exist_ok=True)

    _ensure_artifact_dirs_safe(PROJECT_ROOT, CONFIG)

    global QUARANTINE_DIR, GENERATED_OUTPUT_DIR, SARIF_EXPORT_DIR, AUDIT_LOG_FILE, COVERAGE_REPORTS_DIR, VENV_TEMP_DIR, HTML_REPORTS_DIR
    QUARANTINE_DIR = CONFIG["quarantine_dir"]
    GENERATED_OUTPUT_DIR = CONFIG["generated_output_dir"]
    SARIF_EXPORT_DIR = CONFIG["sarif_export_dir"]
    AUDIT_LOG_FILE = CONFIG["audit_log_file"]
    COVERAGE_REPORTS_DIR = CONFIG["coverage_reports_dir"]
    HTML_REPORTS_DIR = CONFIG["html_reports_dir"]
    VENV_TEMP_DIR = CONFIG["venv_temp_dir"]

    return _deep_freeze(CONFIG)


class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.test_dir = self.tmp.name
        self.config_file = "atco_config.json"
        self.config_path = os.path.join(self.test_dir, self.config_file)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_config(self, content: Any):
        with open(self.config_path, "w", encoding="utf-8") as f:
            if isinstance(content, str):
                f.write(content)
            else:
                json.dump(content, f)

    def test_deep_merge_and_defaults(self):
        # This config is valid and should be merged
        sample_config = {
            "max_parallel_generation": 8,
            "backend_timeouts": {"pynguin": 120},
        }
        self._write_config(sample_config)
        cfg = load_config(self.test_dir, self.config_file)
        self.assertEqual(cfg["max_parallel_generation"], 8)
        self.assertEqual(cfg["backend_timeouts"]["pynguin"], 120)
        self.assertEqual(cfg["retry_backoff_min"], 1)

    def test_path_sanitization_rejection(self):
        with self.assertRaises(ValueError):
            load_config(self.test_dir, "../evil.json")

    def test_invalid_config_schema_falls_back(self):
        # This config has an extra property, which is invalid
        invalid_config = {"new_key": "should be reverted"}
        self._write_config(invalid_config)

        with self.assertLogs(level="WARNING") as cm:
            cfg = load_config(self.test_dir, self.config_file)
            self.assertTrue(any("validation error(s)" in log for log in cm.output))
            self.assertTrue(
                any("Unexpected config keys at '<root>': ['new_key']" in log for log in cm.output)
            )

        self.assertNotIn("new_key", cfg)
        self.assertEqual(cfg["max_parallel_generation"], DEFAULT_CONFIG["max_parallel_generation"])

    def test_immutability(self):
        self._write_config({})
        cfg = load_config(self.test_dir, self.config_file)
        with self.assertRaises(TypeError):
            cfg["new_key"] = "fail"

    def test_deep_merge_with_non_dict_override(self):
        logging.basicConfig(level=logging.WARNING, stream=sys.stdout)
        user_config = {"prioritization_rules": "this should be a dict"}
        self._write_config(user_config)

        with self.assertLogs(level="WARNING") as cm:
            cfg = load_config(self.test_dir, self.config_file)
            # The merge is skipped, so the default value is kept
            self.assertEqual(cfg["prioritization_rules"]["language_boosts"]["python"], 5)
            self.assertTrue(any("Configuration merge conflict" in log for log in cm.output))

    def test_missing_config_file(self):
        cfg = load_config(self.test_dir, "non_existent_config.json")
        self.assertEqual(cfg["max_parallel_generation"], 4)

    def test_empty_config_file(self):
        self._write_config({})
        cfg = load_config(self.test_dir, self.config_file)
        self.assertEqual(cfg["max_parallel_generation"], 4)

    def test_corrupted_json_falls_back(self):
        self._write_config("{'key': 'value'")

        with self.assertLogs(level="WARNING") as cm:
            cfg = load_config(self.test_dir, self.config_file)
            self.assertTrue(any("Failed to load or parse" in log for log in cm.output))

        self.assertEqual(cfg["max_parallel_generation"], 4)

    def test_invalid_top_level_config_type(self):
        # The top level must be an object, not an array
        invalid_config = ["invalid", "config"]
        self._write_config(invalid_config)

        with self.assertLogs(level="WARNING") as cm:
            cfg = load_config(self.test_dir, self.config_file)
            self.assertTrue(
                any("Top-level config must be a JSON object" in log for log in cm.output)
            )

        self.assertEqual(cfg["max_parallel_generation"], DEFAULT_CONFIG["max_parallel_generation"])

    def test_non_dict_with_additional_properties_true(self):
        # Test that validation still works for non-dict keys even if 'additionalProperties' is false
        user_config = {"jira_integration": "not-a-dict"}
        self._write_config(user_config)

        with self.assertLogs(level="WARNING") as cm:
            cfg = load_config(self.test_dir, self.config_file)
            self.assertTrue(
                any("'jira_integration' should be of type 'object'" in log for log in cm.output)
            )
        self.assertNotEqual(cfg["jira_integration"], "not-a-dict")
        self.assertEqual(
            cfg["jira_integration"]["enabled"],
            DEFAULT_CONFIG["jira_integration"]["enabled"],
        )

    def test_invalid_sub_field_reverts_top_level_field_only(self):
        # The sub-field is invalid, so the top-level field should be reverted to default
        user_config = {"jira_integration": {"enabled": "not-a-boolean"}}
        self._write_config(user_config)

        with self.assertLogs(level="WARNING") as cm:
            cfg = load_config(self.test_dir, self.config_file)
            self.assertTrue(
                any("'enabled' should be of type 'boolean'" in log for log in cm.output)
            )
        self.assertEqual(
            cfg["jira_integration"]["enabled"],
            DEFAULT_CONFIG["jira_integration"]["enabled"],
        )

    def test_deep_merge_partial_override(self):
        # Ensure deep merge correctly handles partial overrides
        user_config = {"backend_timeouts": {"jest_llm": 200}}
        self._write_config(user_config)

        cfg = load_config(self.test_dir, self.config_file)
        self.assertEqual(cfg["backend_timeouts"]["jest_llm"], 200)
        self.assertEqual(
            cfg["backend_timeouts"]["pynguin"],
            DEFAULT_CONFIG["backend_timeouts"]["pynguin"],
        )

    def test_deep_merge_with_invalid_subfield(self):
        # If a sub-field is invalid, ensure the entire top-level field is reverted
        user_config = {"prioritization_rules": {"language_boosts": {"python": "not-a-number"}}}
        self._write_config(user_config)

        with self.assertLogs(level="WARNING") as cm:
            cfg = load_config(self.test_dir, self.config_file)
            self.assertTrue(any("'python' should be of type 'number'" in log for log in cm.output))
        self.assertEqual(
            cfg["prioritization_rules"]["language_boosts"]["python"],
            DEFAULT_CONFIG["prioritization_rules"]["language_boosts"]["python"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
