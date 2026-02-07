# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# simulation/plugins/example_plugin.py

import hashlib
import io  # for chunked file reading
import json
import logging
import os
import random
import re
import sys
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List

from cachetools import TTLCache
from pydantic import BaseModel, Field, ValidationError, field_validator

# ---------------------------
# Provisional logger (for early boot messages before plugin logger is configured)
# ---------------------------
_boot_logger = logging.getLogger(__name__)
if not _boot_logger.handlers:
    _boot_logger.setLevel(logging.INFO)
    _boot_handler = logging.StreamHandler(sys.stdout)
    _boot_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    _boot_logger.addHandler(_boot_handler)

# ---------------------------
# Configuration Integration
# ---------------------------
# Load plugin configuration from adjacent JSON file if present; fall back to env/defaults.
CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "configs", "example_plugin_config.json"
)
try:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        PLUGIN_CONFIG = json.load(f)
        _boot_logger.info(f"Loaded plugin config: {CONFIG_PATH}")
except FileNotFoundError:
    _boot_logger.warning(
        "Plugin config file not found. Using environment variables and defaults."
    )
    PLUGIN_CONFIG = {}
except json.JSONDecodeError as e:
    _boot_logger.error(f"Failed to parse plugin config JSON: {e}. Using defaults.")
    PLUGIN_CONFIG = {}

# ---------------------------
# Manifest
# ---------------------------
_DEFAULT_MANIFEST: Dict[str, Any] = {
    "name": "ExampleChaosSecurityPlugin",
    "version": "0.3.0",
    "description": "Provides simulated chaos engineering experiments and basic security audits.",
    "author": "Omnisapient AI Team",
    "compatibility": {
        "min_sim_runner_version": "1.0.0",
        "max_sim_runner_version": "2.0.0",
    },
    "entry_points": {
        "run_custom_chaos_experiment": {
            "description": "Simulates a chaos experiment (CPU hog, network latency, etc.).",
            "parameters": ["target_id", "intensity"],
        },
        "perform_custom_security_audit": {
            "description": "Performs a basic simulated security audit on a given code path.",
            "parameters": ["code_path"],
        },
    },
}
PLUGIN_MANIFEST = _DEFAULT_MANIFEST | PLUGIN_CONFIG.get("manifest", {})


# ---------------------------
# Structured logging
# ---------------------------
class StructuredLogFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "plugin_name": PLUGIN_MANIFEST.get("name"),
            "version": PLUGIN_MANIFEST.get("version"),
            "thread": record.threadName,
        }
        # Optional correlation id, if attached by code
        if hasattr(record, "correlation_id"):
            log_data["correlation_id"] = getattr(record, "correlation_id")
        return json.dumps(log_data, ensure_ascii=False)


plugin_logger = logging.getLogger(PLUGIN_MANIFEST["name"])
if not plugin_logger.handlers:
    plugin_logger.setLevel(
        os.getenv(f"{PLUGIN_MANIFEST['name'].upper()}_LOG_LEVEL", "INFO").upper()
    )
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(StructuredLogFormatter())
    plugin_logger.addHandler(_handler)

# ---------------------------
# Security: secret scanning regex
# ---------------------------
SECRET_PATTERNS = re.compile(
    r"(?i)(password|token|secret|key|jwt|api_key|aws_access_key_id|aws_secret_access_key|gcp_service_account)"
    r"[\"'\s]*[:=][\"'\s]*([a-zA-Z0-9\-_./+=]+)"
)


def _scrub_secrets(content: str) -> str:
    """Redact potential secrets from content (used defensively in logs)."""
    return re.sub(
        r'([_a-zA-Z0-9-]*?(?:key|secret|token|password)[_a-zA-Z0-9-]*?[\s:=][\s:=]?[\'"]?)([^,\]\s\'"]+)([\'"]?)',
        r"\1[REDACTED]\3",
        content,
        flags=re.IGNORECASE,
    )


# ---------------------------
# In-memory cache for security audit results (thread-safe)
# ---------------------------
_audit_cache = TTLCache(
    maxsize=256, ttl=int(os.getenv("SEC_AUDIT_CACHE_TTL_SECONDS", "3600"))
)
_audit_cache_lock = threading.Lock()


def _audit_cache_key(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{path}:{int(st.st_mtime)}:{st.st_size}"
    except FileNotFoundError:
        return f"{path}:missing"


# ---------------------------
# Metrics (production-ready safe wrappers)
# ---------------------------
PROMETHEUS_AVAILABLE = False
try:
    from prometheus_client import Counter, Histogram  # Exposed on default REGISTRY

    PROMETHEUS_AVAILABLE = True
except Exception as _e:
    plugin_logger.warning(f"Prometheus not available; metrics disabled: {_e}")

_METRICS: Dict[str, Any] = {}


def _noop_counter():
    class _Noop:
        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

    return _Noop()


def _noop_histogram():
    class _Noop:
        def labels(self, *args, **kwargs):
            return self

        def observe(self, *args, **kwargs):
            pass

    return _Noop()


def _safe_counter(name: str, doc: str, labelnames: tuple = ()):
    if not PROMETHEUS_AVAILABLE:
        return _noop_counter()
    if name in _METRICS:
        return _METRICS[name]
    try:
        m = Counter(name, doc, labelnames=labelnames)
        _METRICS[name] = m
        return m
    except ValueError:
        plugin_logger.warning(
            f"Metric '{name}' already registered. Using no-op for this instance."
        )
        no = _noop_counter()
        _METRICS[name] = no
        return no


def _safe_histogram(name: str, doc: str, labelnames: tuple = (), buckets=None):
    if not PROMETHEUS_AVAILABLE:
        return _noop_histogram()
    if name in _METRICS:
        return _METRICS[name]
    try:
        m = Histogram(name, doc, labelnames=labelnames, buckets=buckets or Histogram.DEFAULT_BUCKETS)  # type: ignore
        _METRICS[name] = m
        return m
    except ValueError:
        plugin_logger.warning(
            f"Metric '{name}' already registered. Using no-op for this instance."
        )
        no = _noop_histogram()
        _METRICS[name] = no
        return no


# Avoid high-cardinality labels
CHAOS_EXPERIMENT_TOTAL = _safe_counter(
    "plugin_chaos_experiment_total",
    "Total chaos experiments initiated",
    ("status",),
)
SECURITY_FINDINGS_TOTAL = _safe_counter(
    "plugin_security_findings_total",
    "Total security findings detected by this plugin",
    ("severity",),
)
SECURITY_AUDIT_DURATION = _safe_histogram(
    "plugin_security_audit_duration_seconds",
    "Duration of security audits",
    (),
)


# ---------------------------
# Utility: env parsing
# ---------------------------
def _get_env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        plugin_logger.warning(
            f"Invalid float for env {name}: {raw}. Using default {default}."
        )
        return default


def _get_env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------
# Plugin health check
# ---------------------------
def plugin_health() -> Dict[str, Any]:
    """
    Performs a health check on the plugin's dependencies and operational status.
    Returns: dict with status and details.
    """
    status = "ok"
    details: List[str] = []

    # Check CHAOS_INTENSITY_DEFAULT parse-ability
    try:
        _ = float(os.getenv("CHAOS_INTENSITY_DEFAULT", "0.5"))
    except ValueError:
        status = "degraded"
        details.append(
            "CHAOS_INTENSITY_DEFAULT is not a valid float; falling back to 0.5"
        )

    # Check SANCTIONED_CODE_DIR existence
    sanctioned_dir = os.getenv("SANCTIONED_CODE_DIR", os.getcwd())
    if not os.path.isdir(sanctioned_dir):
        status = "degraded"
        details.append(f"SANCTIONED_CODE_DIR does not exist: {sanctioned_dir}")

    # Prometheus availability
    if not PROMETHEUS_AVAILABLE:
        details.append("Prometheus metrics are disabled.")

    plugin_logger.info(f"Plugin health check: {status} | details={details}")
    return {"status": status, "details": details}


# ---------------------------
# Plugin functionality: Chaos Experiment
# ---------------------------
class ChaosExperimentParams(BaseModel):
    target_id: str = Field(..., min_length=1)
    intensity: float = Field(0.5, ge=0.0, le=1.0)  # 0..1


def run_custom_chaos_experiment(
    target_id: str, intensity: float = 0.5
) -> Dict[str, Any]:
    """
    Simulates a custom chaos experiment.
    @param target_id: The ID of the system or agent to target.
    @param intensity: A float from 0.0 to 1.0 representing the intensity of the chaos.
    @return A dictionary with the outcome of the experiment.
    """
    correlation_id = hashlib.sha256(
        f"{target_id}:{time.time_ns()}".encode("utf-8")
    ).hexdigest()[:12]

    # Validate inputs
    try:
        params = ChaosExperimentParams(target_id=target_id, intensity=float(intensity))
        intensity = params.intensity
        target_id = params.target_id
    except ValidationError as e:
        CHAOS_EXPERIMENT_TOTAL.labels(status="validation_error").inc()
        plugin_logger.error(
            f"Input validation failed for chaos experiment: {e}",
            extra={"correlation_id": correlation_id},
        )
        return {
            "plugin_name": PLUGIN_MANIFEST["name"],
            "status": "ERROR",
            "message": f"Input validation failed: {e}",
            "correlation_id": correlation_id,
        }

    # Controls
    enable_chaos = _get_env_bool("ENABLE_CHAOS_EXPERIMENTS", True)
    failure_prob = _get_env_float("CHAOS_SIMULATE_FAILURE_PROB", 0.1)
    default_threshold = _get_env_float("CHAOS_INTENSITY_DEFAULT", 0.7)

    plugin_logger.info(
        f"Running chaos experiment on {target_id} with intensity {intensity} "
        f"(threshold={default_threshold}, chaos_enabled={enable_chaos})",
        extra={"correlation_id": correlation_id},
    )

    try:
        # Simulate failure only if chaos enabled
        if enable_chaos and random.random() < failure_prob:
            raise ConnectionError("Simulated API connection failure.")

        if intensity > default_threshold:
            outcome = "FAILURE_INJECTED"
            message = "High intensity chaos experiment caused a simulated failure."
        else:
            outcome = "EXPERIMENT_COMPLETED"
            message = (
                "Low intensity chaos experiment completed without significant impact."
            )

        CHAOS_EXPERIMENT_TOTAL.labels(status=outcome).inc()
        return {
            "plugin_name": PLUGIN_MANIFEST["name"],
            "status": outcome,
            "target": target_id,
            "intensity": intensity,
            "message": message,
            "correlation_id": correlation_id,
        }
    except Exception as e:
        CHAOS_EXPERIMENT_TOTAL.labels(status="error").inc()
        plugin_logger.error(
            f"Error during chaos experiment on {_scrub_secrets(target_id)}: {e}",
            extra={"correlation_id": correlation_id},
        )
        return {
            "plugin_name": PLUGIN_MANIFEST["name"],
            "status": "ERROR",
            "target": target_id,
            "intensity": intensity,
            "message": f"An error occurred during experiment: {e}",
            "correlation_id": correlation_id,
        }


# ---------------------------
# Plugin functionality: Security Audit
# ---------------------------
class SecurityAuditParams(BaseModel):
    code_path: str = Field(
        ..., min_length=1, description="Path to the code file for security audit."
    )

    @field_validator("code_path")
    @classmethod
    def prevent_path_traversal(cls, v: str) -> str:
        # Basic guard; real guard is enforced via sanctioned directory normalization below.
        if ".." in v:
            raise ValueError("Path traversal detected in code_path")
        return v


def perform_custom_security_audit(code_path: str) -> Dict[str, Any]:
    """
    Performs a custom security audit on a given code path.
    @param code_path: A relative or absolute path to the file to audit.
    @return A dictionary with the audit findings.
    """
    # Validate and normalize path within sanctioned directory
    try:
        params = SecurityAuditParams(code_path=code_path)
        code_path = params.code_path

        sanctioned_dir = os.getenv("SANCTIONED_CODE_DIR", os.getcwd())
        sanctioned_dir_abs = os.path.abspath(sanctioned_dir)
        # Normalize via join + abspath; join will ignore sanctioned_dir if code_path is absolute
        candidate = os.path.abspath(os.path.join(sanctioned_dir_abs, code_path))
        # Resolve symlinks defensively
        candidate_real = os.path.realpath(candidate)
        sanctioned_real = os.path.realpath(sanctioned_dir_abs)
        if (
            not candidate_real.startswith(sanctioned_real + os.sep)
            and candidate_real != sanctioned_real
        ):
            raise PermissionError(
                f"File access denied: {code_path} is outside sanctioned directory."
            )
    except (ValidationError, PermissionError) as e:
        SECURITY_FINDINGS_TOTAL.labels(severity="ERROR").inc()
        plugin_logger.error(f"Input validation/path error for security audit: {e}")
        return {
            "plugin_name": PLUGIN_MANIFEST["name"],
            "status": "ERROR",
            "code_path": code_path,
            "findings": [],
            "severity": "None",
            "message": f"Input validation/path error: {e}",
        }

    # Optional: skip overly large files
    max_mb = _get_env_float("MAX_AUDIT_FILE_MB", 5.0)
    try:
        sz_bytes = os.path.getsize(candidate_real)
        if sz_bytes > max_mb * 1024 * 1024:
            plugin_logger.warning(
                f"Skipping audit for large file (> {max_mb} MB): {candidate_real}"
            )
            return {
                "plugin_name": PLUGIN_MANIFEST["name"],
                "status": "SKIPPED_TOO_LARGE",
                "code_path": code_path,
                "findings": [],
                "severity": "None",
                "message": f"File exceeds size limit of {max_mb} MB",
            }
    except FileNotFoundError:
        pass

    # Cache check
    cache_key = _audit_cache_key(candidate_real)
    with _audit_cache_lock:
        cached = _audit_cache.get(cache_key)
    if cached:
        return cached

    start = time.monotonic()
    findings: List[Dict[str, Any]] = []
    overall_severity = "None"

    try:
        if not os.path.exists(candidate_real):
            raise FileNotFoundError(f"Code path not found for audit: {candidate_real}")

        # Scan line by line (streaming)
        with io.open(candidate_real, "r", encoding="utf-8", errors="ignore") as f:
            for line_num, line in enumerate(f, 1):
                # Secrets
                secrets_match = SECRET_PATTERNS.search(line)
                if secrets_match:
                    findings.append(
                        {
                            "line": line_num,
                            "text": f"Hardcoded secret detected: '{secrets_match.group(1)}'",
                            "severity": "High",
                            "rule": "hardcoded_secret",
                        }
                    )
                    overall_severity = "High"

                # Dangerous functions
                if "eval(" in line or "exec(" in line:
                    findings.append(
                        {
                            "line": line_num,
                            "text": "Use of eval/exec detected (potential code injection).",
                            "severity": "High",
                            "rule": "eval_exec",
                        }
                    )
                    overall_severity = "High"

                # Command execution patterns
                if re.search(r"\bos\.system\s*\(", line) or re.search(
                    r"\bsubprocess\.(Popen|call|run)\s*\(", line
                ):
                    findings.append(
                        {
                            "line": line_num,
                            "text": "Potential command execution detected (os.system/subprocess). Review for injection.",
                            "severity": "High",
                            "rule": "cmd_exec",
                        }
                    )
                    overall_severity = "High"

                # Insecure YAML load (no SafeLoader)
                if re.search(r"\byaml\.load\s*\(", line) and "SafeLoader" not in line:
                    findings.append(
                        {
                            "line": line_num,
                            "text": "yaml.load without SafeLoader detected (unsafe deserialization).",
                            "severity": "High",
                            "rule": "yaml_load",
                        }
                    )
                    overall_severity = "High"

                # Insecure pickle usage
                if re.search(r"\bpickle\.loads?\s*\(", line):
                    findings.append(
                        {
                            "line": line_num,
                            "text": "pickle load detected (unsafe deserialization of untrusted data).",
                            "severity": "High",
                            "rule": "pickle_load",
                        }
                    )
                    overall_severity = "High"

                # Sensitive imports (heuristic)
                if re.search(r"^\s*import\s+(os|subprocess|shutil|requests)\b", line):
                    findings.append(
                        {
                            "line": line_num,
                            "text": "Sensitive import detected; review usage for potential risks.",
                            "severity": "Medium",
                            "rule": "sensitive_import",
                        }
                    )
                    if overall_severity not in ("High", "Critical"):
                        overall_severity = "Medium"

        status = "FINDINGS_DETECTED" if findings else "COMPLETED"
        duration = time.monotonic() - start
        SECURITY_AUDIT_DURATION.labels().observe(duration)
        SECURITY_FINDINGS_TOTAL.labels(severity=overall_severity).inc()

        result = {
            "plugin_name": PLUGIN_MANIFEST["name"],
            "status": status,
            "code_path": code_path,
            "findings": findings,
            "severity": overall_severity,
            "message": (
                "Security audit completed."
                if findings == []
                else "Security audit completed with findings."
            ),
            "duration_seconds": round(duration, 3),
        }

        with _audit_cache_lock:
            _audit_cache[cache_key] = result

        plugin_logger.info(
            f"Security audit on {code_path} completed in {duration:.2f}s with severity={overall_severity}."
        )
        return result

    except FileNotFoundError:
        plugin_logger.error(f"File not found during audit: {candidate_real}")
        return {
            "plugin_name": PLUGIN_MANIFEST["name"],
            "status": "ERROR",
            "code_path": code_path,
            "findings": [],
            "severity": "None",
            "message": f"File not found: {candidate_real}",
        }
    except PermissionError as e:
        plugin_logger.error(f"Permission denied during audit of {candidate_real}: {e}")
        return {
            "plugin_name": PLUGIN_MANIFEST["name"],
            "status": "ERROR",
            "code_path": code_path,
            "findings": [],
            "severity": "High",
            "message": f"Permission denied for file access: {e}",
        }
    except Exception as e:
        plugin_logger.error(
            f"Error during security audit of {_scrub_secrets(code_path)}: {e}"
        )
        return {
            "plugin_name": PLUGIN_MANIFEST["name"],
            "status": "ERROR",
            "code_path": code_path,
            "findings": [],
            "severity": "None",
            "message": f"An unexpected error occurred during audit: {e}",
        }


# ---------------------------
# Dashboard Panel for this Plugin
# ---------------------------
def example_plugin_dashboard_panel(st_dash_obj: Any, current_result: Dict[str, Any]):
    """
    Streamlit render function for this plugin's dashboard panel.
    @param st_dash_obj: The Streamlit object to render to.
    @param current_result: The full result dictionary from the latest simulation run.
    """
    st_dash_obj.markdown("#### Example Plugin Metrics")

    last_chaos_run = current_result.get("mutation", {}).get("chaos", {})
    if last_chaos_run and last_chaos_run.get("plugin_name") == PLUGIN_MANIFEST["name"]:
        st_dash_obj.write(
            f"**Last Chaos Experiment Status:** {last_chaos_run.get('status', 'N/A')}"
        )
        st_dash_obj.write(
            f"Target: {last_chaos_run.get('target', 'N/A')}, Intensity: {last_chaos_run.get('intensity', 'N/A')}"
        )
        st_dash_obj.info(last_chaos_run.get("message", ""))
    else:
        st_dash_obj.info(
            "No chaos experiment data from this plugin in the current result."
        )

    st_dash_obj.markdown("---")

    st_dash_obj.markdown("#### Simulated Security Audit Insights")
    audit_findings = [
        {"file": "app.py", "finding": "Hardcoded API key", "severity": "High"},
        {
            "file": "utils.py",
            "finding": "Insecure temporary file creation",
            "severity": "Medium",
        },
    ]

    if audit_findings:
        st_dash_obj.write("Potential Security Findings:")
        for finding in audit_findings:
            st_dash_obj.warning(
                f"- **{finding['severity']}**: {finding['finding']} in `{finding['file']}`"
            )
    else:
        st_dash_obj.success("No critical security findings detected (simulated).")

    if st_dash_obj.session_state.get("enable_live_data", False):
        st_dash_obj.text(
            f"Live data enabled for this panel. Current time: {datetime.now().strftime('%H:%M:%S')}"
        )


# ---------------------------
# Registration hook for dashboard
# ---------------------------
def register_my_dashboard_panels(register_func: Callable):
    """
    Called by the dashboard module to register panels.
    @param register_func: The registration function from the core dashboard.
    """
    plugin_logger.info(
        "Registering dashboard panels from ExampleChaosSecurityPlugin..."
    )
    register_func(
        panel_id="example_plugin_metrics",
        title="Chaos & Security Plugin Insights",
        render_function=example_plugin_dashboard_panel,
        description="Displays metrics and findings from the example chaos and security plugin.",
        roles=["admin", "auditor"],
        live_data_supported=True,
    )


# ---------------------------
# Compatibility check helper
# ---------------------------
def check_compatibility(core_version: str) -> bool:
    """
    Checks if the plugin is compatible with the given core system version.
    @param core_version: The version string of the core system.
    @return True if compatible, False otherwise (or warn if above max).
    """

    def _vt(v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.split("."))
        except Exception:
            return (0, 0, 0)

    min_ver = PLUGIN_MANIFEST["compatibility"].get("min_sim_runner_version")
    max_ver = PLUGIN_MANIFEST["compatibility"].get("max_sim_runner_version")
    cur = _vt(core_version)

    if min_ver and cur < _vt(min_ver):
        plugin_logger.error(
            f"Plugin {PLUGIN_MANIFEST['name']} (v{PLUGIN_MANIFEST['version']}) "
            f"requires sim_runner v{min_ver} or higher. Current: v{core_version}"
        )
        return False

    if max_ver and cur > _vt(max_ver):
        plugin_logger.warning(
            f"Plugin {PLUGIN_MANIFEST['name']} (v{PLUGIN_MANIFEST['version']}) "
            f"is designed for sim_runner up to v{max_ver}. Current: v{core_version}. "
            f"Compatibility issues may arise."
        )
        return True

    plugin_logger.info(
        f"Plugin {PLUGIN_MANIFEST['name']} (v{PLUGIN_MANIFEST['version']}) "
        f"is compatible with sim_runner v{core_version}."
    )
    return True
