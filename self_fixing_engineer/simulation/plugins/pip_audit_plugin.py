# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# plugins/pip_audit_plugin.py

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# --- Logger Setup (First!) ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# --- Dynamic imports for optional dependencies ---
try:
    from pydantic import BaseModel, Field, ValidationError

    pydantic_available = True
except ImportError:
    pydantic_available = False

try:
    # Prometheus client (with access to REGISTRY for metric reuse)
    from prometheus_client import REGISTRY, Counter, Histogram

    prometheus_available = True
except ImportError:
    prometheus_available = False
    logger.warning(
        "Prometheus client not found. Metrics for pip-audit plugin will be disabled."
    )

try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    tenacity_available = True
except ImportError:
    tenacity_available = False
    logger.warning(
        "tenacity not found. Retry/backoff for pip-audit plugin will be disabled."
    )

    def retry(*args, **kwargs):  # type: ignore
        return lambda f: f

    def stop_after_attempt(n):  # type: ignore
        return None

    def wait_exponential(*args, **kwargs):  # type: ignore
        return None

    def retry_if_exception_type(e):  # type: ignore
        return lambda x: False


try:
    from detect_secrets.core import SecretsCollection

    detect_secrets_available = True
except ImportError:
    detect_secrets_available = False

try:
    from redis.asyncio import Redis  # Use async redis client

    redis_available = True
except ImportError:
    redis_available = False


# --- Pydantic Config Model ---
if pydantic_available:

    class PipAuditConfig(BaseModel):
        pip_audit_cli_path: str = Field(default="pip-audit")
        default_scan_method: str = Field(
            default="installed", pattern="^(installed|requirements)$"
        )
        default_timeout_seconds: int = Field(default=300, ge=1)
        retry_attempts: int = Field(default=2, ge=0)
        retry_backoff_factor: float = Field(default=2.0, ge=0)
        max_log_output_size_kb: int = Field(default=512, ge=1)
        redis_cache_url: Optional[str] = None
        redis_cache_ttl: int = Field(default=3600, ge=1)
        scrub_raw_output: bool = Field(
            default=False,
            description="Scrub potential secrets from raw stdout/stderr before returning/caching",
        )

    _default_config = PipAuditConfig()
else:

    class PipAuditConfig:  # minimal fallback
        def __init__(self):
            self.pip_audit_cli_path = "pip-audit"
            self.default_scan_method = "installed"
            self.default_timeout_seconds = 300
            self.retry_attempts = 2
            self.retry_backoff_factor = 2.0
            self.max_log_output_size_kb = 512
            self.redis_cache_url = None
            self.redis_cache_ttl = 3600
            self.scrub_raw_output = False

    _default_config = PipAuditConfig()


# --- Load Config from File or Env ---
def _load_config() -> PipAuditConfig:
    config_file_path = Path(__file__).parent / "configs" / "pip_audit_config.json"
    config_dict: Dict[str, Any] = {}
    if config_file_path.exists():
        try:
            with open(config_file_path, "r", encoding="utf-8") as f:
                config_dict = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(
                f"Could not load config file {config_file_path}: {e}. Using environment variables and defaults."
            )

    # Derive keys and types from the defaults (robust for both pydantic and fallback)
    base_items = vars(_default_config)
    for key, default_val in base_items.items():
        env_var = os.getenv(f"PIP_AUDIT_{key.upper()}")
        if env_var is None:
            continue
        try:
            # Cast based on default value's type
            if isinstance(default_val, bool):
                config_dict[key] = env_var.lower() in ("true", "1", "t", "yes", "y")
            elif isinstance(default_val, int):
                config_dict[key] = int(env_var)
            elif isinstance(default_val, float):
                config_dict[key] = float(env_var)
            else:
                config_dict[key] = env_var
        except ValueError:
            logger.warning(
                f"Invalid type for environment variable PIP_AUDIT_{key.upper()}. Using default.",
                exc_info=False,
            )

    if pydantic_available:
        try:
            cfg = PipAuditConfig.parse_obj(config_dict)
        except Exception as e:  # Catching a mock ValidationError is not allowed
            logger.error(f"Configuration validation failed: {e}. Using defaults.")
            cfg = PipAuditConfig()
    else:
        cfg = PipAuditConfig()
        cfg.__dict__.update(config_dict)

    # Default to scrubbing in CI unless explicitly overridden by env
    if "PIP_AUDIT_SCRUB_RAW_OUTPUT" not in os.environ and (
        os.getenv("CI") or os.getenv("GITHUB_ACTIONS")
    ):
        try:
            # Pydantic or fallback both support attribute assignment
            cfg.scrub_raw_output = True
        except Exception:
            pass

    return cfg


PIP_AUDIT_CONFIG = _load_config()


# --- Prometheus Metrics (Idempotent Definition, try to reuse existing) ---
_METRICS: Dict[str, Any] = {}
if prometheus_available:

    class _NoopMetric:
        def labels(self, *a, **k):
            return self

        def inc(self, *a, **k):
            pass

        def observe(self, *a, **k):
            pass

    def _get_or_create_metric(factory, name: str, documentation: str, **kwargs):
        # Return cached instance if available
        metric = _METRICS.get(name)
        if metric is not None:
            return metric
        try:
            metric = factory(name, documentation, **kwargs)
            _METRICS[name] = metric
            return metric
        except ValueError:
            # If already registered elsewhere, try to reuse from REGISTRY (best-effort)
            try:
                existing = getattr(REGISTRY, "_names_to_collectors", {}).get(
                    name
                )  # private but pragmatic
                if existing is not None:
                    _METRICS[name] = existing
                    logger.info(f"Reusing already-registered Prometheus metric: {name}")
                    return existing
            except Exception:
                pass
            logger.warning(
                f"Metric '{name}' is already registered and cannot be reused portably; disabling local metric."
            )
            metric = _NoopMetric()
            _METRICS[name] = metric
            return metric

    # Keep labels low-cardinality
    PIP_AUDIT_SCANS_TOTAL = _get_or_create_metric(
        Counter,
        "pip_audit_scans_total",
        "Total pip-audit scans performed",
        labelnames=("status", "reason", "method"),
    )
    PIP_AUDIT_VULNERABILITIES_DETECTED = _get_or_create_metric(
        Counter,
        "pip_audit_vulnerabilities_detected_total",
        "Total vulnerabilities detected by pip-audit",
        labelnames=("severity",),
    )
    PIP_AUDIT_LATENCY_SECONDS = _get_or_create_metric(
        Histogram,
        "pip_audit_latency_seconds",
        "Latency of pip-audit scans",
        labelnames=("method",),
    )
    PIP_AUDIT_ERRORS_TOTAL = _get_or_create_metric(
        Counter,
        "pip_audit_errors_total",
        "Total errors during pip-audit scans",
        labelnames=("error_type",),
    )
    DEPENDENCIES_SCANNED = _get_or_create_metric(
        Counter,
        "pip_audit_dependencies_scanned_total",
        "Total dependencies scanned",
        labelnames=("scan_method",),
    )
else:

    class DummyMetric:
        # Add DEFAULT_BUCKETS to match Histogram.DEFAULT_BUCKETS
        DEFAULT_BUCKETS = (
            0.005,
            0.01,
            0.025,
            0.05,
            0.075,
            0.1,
            0.25,
            0.5,
            0.75,
            1.0,
            2.5,
            5.0,
            7.5,
            10.0,
            float("inf"),
        )

        def inc(self, amount: float = 1.0):
            pass

        def set(self, value: float):
            pass

        def observe(self, value: float):
            pass

        def labels(self, *args, **kwargs):
            return self

    PIP_AUDIT_SCANS_TOTAL = PIP_AUDIT_VULNERABILITIES_DETECTED = (
        PIP_AUDIT_LATENCY_SECONDS
    ) = PIP_AUDIT_ERRORS_TOTAL = DEPENDENCIES_SCANNED = DummyMetric()


PLUGIN_MANIFEST = {
    "name": "PipAuditPlugin",
    "version": "1.1.1",
    "description": "Integrates with pip-audit to scan Python dependencies for known vulnerabilities.",
    "author": "Self-Fixing Engineer Team",
    "capabilities": ["dependency_vulnerability_scanning", "security_auditing"],
    "permissions_required": ["process_execution", "filesystem_read"],
    "compatibility": {
        "min_sim_runner_version": "1.0.0",
        "max_sim_runner_version": "2.0.0",
    },
    "entry_points": {
        "scan_dependencies": {
            "description": "Scans Python dependencies (from requirements.txt or installed env) for vulnerabilities.",
            "parameters": ["target_path", "scan_method", "pip_audit_args"],
        }
    },
    "health_check": "plugin_health",
    "api_version": "v1",
    "license": "MIT",
    "homepage": "https://www.self-fixing.engineer",
    "tags": ["security", "pip-audit", "dependencies", "vulnerabilities", "python"],
}


# --- Audit Logger Integration (Conceptual) ---
try:
    from simulation.audit_log import AuditLogger as SFE_AuditLogger

    _sfe_audit_logger = SFE_AuditLogger.from_environment()
except ImportError:
    logger.warning(
        "SFE AuditLogger not found. Audit events will be logged to plugin's logger only."
    )

    class MockAuditLogger:
        async def log(self, event_type: str, details: Dict[str, Any], **kwargs: Any):
            logger.info(f"[AUDIT_MOCK] {event_type}: {details}")

    _sfe_audit_logger = MockAuditLogger()


def _scrub_secrets(data: Union[str, Dict, List]) -> Union[str, Dict, List]:
    if not detect_secrets_available:
        return data
    try:
        if isinstance(data, str):
            secrets = SecretsCollection()
            secrets.scan_string(data)
            # best-effort replacement
            for secret in secrets:
                val = getattr(secret, "secret_value", None)
                if val:
                    data = data.replace(val, "[REDACTED]")
            return data
        if isinstance(data, dict):
            return {k: _scrub_secrets(v) for k, v in data.items()}
        if isinstance(data, list):
            return [_scrub_secrets(item) for item in data]
    except Exception:
        return data
    return data


async def _audit_event(event_type: str, details: Dict[str, Any]):
    scrubbed_details = _scrub_secrets(details)
    await _sfe_audit_logger.log(event_type, scrubbed_details)


async def _which(cmd: str) -> Optional[str]:
    """Finds the path to an executable using 'which' or 'where'."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "which" if os.name != "nt" else "where",
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip().split("\n")[0] if proc.returncode == 0 else None
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        logger.debug(f"Error finding executable '{cmd}': {e}")
        return None
    except Exception as e:
        logger.debug(f"An unexpected error occurred while finding '{cmd}': {e}")
        return None


class TransientScanError(Exception):
    """Used to trigger retry for transient scan failures."""

    pass


# Cache of 'pip-audit --version' to avoid repeated subprocess
_pip_audit_version_cache: Dict[Tuple[str, ...], str] = {}


async def _get_pip_audit_version(base_cmd: List[str]) -> str:
    key = tuple(base_cmd) + ("--version",)
    if key in _pip_audit_version_cache:
        return _pip_audit_version_cache[key]
    try:
        proc = await asyncio.create_subprocess_exec(
            *base_cmd,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            vline = stdout.decode(errors="replace").strip().splitlines()[0]
            _pip_audit_version_cache[key] = vline
            return vline
    except Exception:
        pass
    return "unknown"


async def _pip_freeze_hash(python_executable: Optional[str]) -> Optional[str]:
    """Return a stable hash of the current environment's installed packages via 'pip freeze'."""
    py = python_executable or sys.executable
    try:
        proc = await asyncio.create_subprocess_exec(
            py,
            "-m",
            "pip",
            "freeze",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        if proc.returncode == 0:
            h = hashlib.sha256(stdout).hexdigest()
            return h
    except Exception:
        return None
    return None


async def plugin_health(python_executable: Optional[str] = None) -> Dict[str, Any]:
    """
    Performs a health check on the pip-audit plugin and its dependencies.

    This function verifies that the 'pip-audit' command-line tool is available in the system's PATH
    and can be executed. If a python_executable is provided, it also validates module-mode
    invocation via `{python_executable} -m pip_audit --version`.
    """
    status = "ok"
    details: List[str] = []

    # Check CLI mode
    pip_audit_path = await _which(PIP_AUDIT_CONFIG.pip_audit_cli_path)
    cli_ok = False
    if not pip_audit_path:
        details.append(f"'{PIP_AUDIT_CONFIG.pip_audit_cli_path}' not found in PATH.")
    else:
        details.append(f"pip-audit CLI found at: {pip_audit_path}")
        try:
            proc = await asyncio.create_subprocess_exec(
                pip_audit_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                version_line = stdout.decode().strip().splitlines()[0]
                details.append(f"pip-audit --version successful: {version_line}")
                cli_ok = True
            else:
                details.append(f"pip-audit --version failed: {stderr.decode().strip()}")
        except Exception as e:
            details.append(f"pip-audit CLI execution failed: {e}")

    # Check module mode (optional)
    module_ok = False
    if python_executable:
        try:
            proc = await asyncio.create_subprocess_exec(
                python_executable,
                "-m",
                "pip_audit",
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                version_line = stdout.decode().strip().splitlines()[0]
                details.append(
                    f"Module mode OK: {python_executable} -m pip_audit --version: {version_line}"
                )
                module_ok = True
            else:
                details.append(
                    f"Module mode failed: rc={proc.returncode} stderr={stderr.decode().strip()}"
                )
        except Exception as e:
            details.append(f"Module mode execution failed: {e}")

    if not (cli_ok or module_ok):
        # If neither mode works, mark degraded/error
        status = "error" if not pip_audit_path else "degraded"

    logger.info(f"Plugin health check: {status}")
    return {"status": status, "details": details}


def _parse_severity_from_description(description: str) -> str:
    """Extracts severity from a vulnerability description string."""
    if not description:
        return "UNKNOWN"
    d = description.upper()
    if "CRITICAL" in d:
        return "CRITICAL"
    if "HIGH" in d or "DENIAL OF SERVICE" in d or "RCE" in d:
        return "HIGH"
    if "MEDIUM" in d or "ARBITRARY CODE EXECUTION" in d:
        return "MEDIUM"
    if "LOW" in d:
        return "LOW"
    return "UNKNOWN"


def _validate_safe_args(args: List[str]) -> List[str]:
    """
    Validates command-line arguments to prevent injection attacks.
    Since we never use shell=True, we only reject NUL and newline which can break argv.
    """
    safe_args = []
    for arg in args:
        if any(c in arg for c in ["\x00", "\n", "\r"]):
            raise ValueError(
                f"Invalid control character in pip-audit argument: {arg!r}"
            )
        safe_args.append(arg)
    return safe_args


async def _get_cached_result(cache_key: str) -> Optional[Dict[str, Any]]:
    if not redis_available or not PIP_AUDIT_CONFIG.redis_cache_url:
        return None
    redis = None
    try:
        redis = Redis.from_url(PIP_AUDIT_CONFIG.redis_cache_url, decode_responses=True)
        cached_result = await redis.get(cache_key)
        if cached_result:
            logger.info(f"Returning cached result for key: {cache_key}")
            data = json.loads(cached_result)
            # Ensure payload parity (raw_output omitted in cache)
            data.setdefault("raw_output", None)
            return data
    except Exception as e:
        logger.error(f"Failed to retrieve from Redis cache: {e}", exc_info=True)
    finally:
        try:
            if redis:
                await redis.aclose()
        except Exception:
            pass
    return None


async def _cache_scan_result(cache_key: str, result: Dict[str, Any]):
    if not redis_available or not PIP_AUDIT_CONFIG.redis_cache_url:
        return
    redis = None
    try:
        # Avoid caching raw_output to reduce size; preserve shape parity with raw_output=None
        slim = dict(result)
        slim["raw_output"] = None
        redis = Redis.from_url(PIP_AUDIT_CONFIG.redis_cache_url, decode_responses=True)
        await redis.set(
            cache_key, json.dumps(slim), ex=PIP_AUDIT_CONFIG.redis_cache_ttl
        )
        logger.info(f"Cached result for key: {cache_key}")
    except Exception as e:
        logger.error(f"Failed to set Redis cache: {e}", exc_info=True)
    finally:
        try:
            if redis:
                await redis.aclose()
        except Exception:
            pass


def _build_cache_key(payload: Dict[str, Any]) -> str:
    # Stable JSON-based SHA256 key
    return (
        "pip_audit:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    )


def _trim_and_optionally_scrub(stdout_data: str, stderr_data: str) -> Tuple[str, str]:
    max_bytes = PIP_AUDIT_CONFIG.max_log_output_size_kb * 1024
    out = stdout_data[:max_bytes]
    err = stderr_data[:max_bytes]
    if PIP_AUDIT_CONFIG.scrub_raw_output and detect_secrets_available:
        out = _scrub_secrets(out)  # best effort
        err = _scrub_secrets(err)
    return out, err


@retry(
    stop=stop_after_attempt(PIP_AUDIT_CONFIG.retry_attempts),
    wait=wait_exponential(
        multiplier=PIP_AUDIT_CONFIG.retry_backoff_factor, min=1, max=10
    ),
    retry=(
        retry_if_exception_type((TransientScanError, asyncio.TimeoutError))
        if tenacity_available
        else None
    ),
)
async def scan_dependencies(
    target_path: Optional[str] = None,
    scan_method: str = PIP_AUDIT_CONFIG.default_scan_method,
    pip_audit_args: Optional[List[str]] = None,
    python_executable: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Scans Python dependencies using pip-audit.
    - If python_executable is provided, runs as a module: {python_executable} -m pip_audit
    - Otherwise runs the CLI resolved by PATH (pip-audit)
    """
    scan_id = f"pip-audit-scan-{uuid.uuid4().hex[:8]}"
    start_time = time.monotonic()

    # Resolve invocation mode and version
    pip_audit_cli_path = await _which(PIP_AUDIT_CONFIG.pip_audit_cli_path)
    if python_executable:
        base_cmd = [python_executable, "-m", "pip_audit"]
        invocation_mode = "module"
    else:
        if not pip_audit_cli_path:
            error_msg = f"'{PIP_AUDIT_CONFIG.pip_audit_cli_path}' not found in PATH."
            logger.error(f"[{scan_id}] {error_msg}")
            PIP_AUDIT_ERRORS_TOTAL.labels(error_type="cli_not_found").inc()
            PIP_AUDIT_SCANS_TOTAL.labels(
                status="failed", reason="cli_not_found", method=scan_method
            ).inc()
            return {
                "success": False,
                "reason": error_msg,
                "vulnerabilities_found": False,
                "vulnerability_count": 0,
                "vulnerabilities": [],
                "raw_output": "",
                "target_path": target_path,
                "scan_id": scan_id,
            }
        base_cmd = [pip_audit_cli_path]
        invocation_mode = "cli"

    version_str = await _get_pip_audit_version(base_cmd)

    # Prepare arguments
    final_pip_audit_args: List[str] = ["--format", "json"]
    if pip_audit_args:
        try:
            final_pip_audit_args.extend(_validate_safe_args(pip_audit_args))
        except ValueError as e:
            error_msg = f"Invalid pip-audit arguments: {e}"
            PIP_AUDIT_ERRORS_TOTAL.labels(error_type="invalid_args").inc()
            PIP_AUDIT_SCANS_TOTAL.labels(
                status="failed", reason="invalid_args", method=scan_method
            ).inc()
            return {
                "success": False,
                "reason": error_msg,
                "vulnerabilities_found": False,
                "vulnerability_count": 0,
                "vulnerabilities": [],
                "raw_output": "",
                "target_path": target_path,
                "scan_id": scan_id,
            }

    # Build command based on method and normalize CWD/paths
    cwd = Path.cwd()
    cache_payload: Dict[str, Any] = {
        "method": scan_method,
        "args": final_pip_audit_args,
        "invocation": invocation_mode,
        "py": python_executable or "cli",
        "pa_version": version_str,
        "target": None,
    }
    cmd: List[str] = list(base_cmd)

    if scan_method == "installed":
        DEPENDENCIES_SCANNED.labels(scan_method="installed").inc()
        if target_path and Path(target_path).is_dir():
            cwd = Path(target_path)
        cmd.extend(final_pip_audit_args)
        env_fp = await _pip_freeze_hash(python_executable)
        cache_payload["env_fp"] = env_fp
    elif scan_method == "requirements":
        DEPENDENCIES_SCANNED.labels(scan_method="requirements").inc()
        target_path_obj = Path(target_path or "")
        if not target_path_obj.exists():
            error_msg = f"Requirements file or directory not found at: {target_path}"
            logger.error(f"[{scan_id}] {error_msg}")
            PIP_AUDIT_ERRORS_TOTAL.labels(
                error_type="requirements_file_not_found"
            ).inc()
            PIP_AUDIT_SCANS_TOTAL.labels(
                status="failed", reason="requirements_not_found", method=scan_method
            ).inc()
            return {
                "success": False,
                "reason": error_msg,
                "vulnerabilities_found": False,
                "vulnerability_count": 0,
                "vulnerabilities": [],
                "raw_output": "",
                "target_path": target_path,
                "scan_id": scan_id,
            }

        final_requirements_file = target_path_obj
        if target_path_obj.is_dir():
            # Discover a requirements file; default to requirements.txt
            candidate = target_path_obj / "requirements.txt"
            if not candidate.exists():
                error_msg = f"No requirements.txt found in directory: {target_path}"
                logger.error(f"[{scan_id}] {error_msg}")
                PIP_AUDIT_ERRORS_TOTAL.labels(
                    error_type="requirements_file_not_found_in_dir"
                ).inc()
                PIP_AUDIT_SCANS_TOTAL.labels(
                    status="failed",
                    reason="requirements_not_found_in_dir",
                    method=scan_method,
                ).inc()
                return {
                    "success": False,
                    "reason": error_msg,
                    "vulnerabilities_found": False,
                    "vulnerability_count": 0,
                    "vulnerabilities": [],
                    "raw_output": "",
                    "target_path": target_path,
                    "scan_id": scan_id,
                }
            final_requirements_file = candidate

        cmd.extend(["--requirements", str(final_requirements_file)])
        cmd.extend(final_pip_audit_args)
        cwd = (
            final_requirements_file.parent
            if final_requirements_file.is_file()
            else target_path_obj
        )

        try:
            data = final_requirements_file.read_bytes()
            cache_payload["target"] = str(final_requirements_file.resolve())
            cache_payload["req_sha256"] = hashlib.sha256(data).hexdigest()
            cache_payload["req_mtime"] = int(final_requirements_file.stat().st_mtime)
        except Exception:
            # best effort
            cache_payload["target"] = str(final_requirements_file)
    else:
        error_msg = f"Unsupported scan_method: {scan_method}. Choose 'installed' or 'requirements'."
        logger.error(f"[{scan_id}] {error_msg}")
        PIP_AUDIT_ERRORS_TOTAL.labels(error_type="unsupported_method").inc()
        PIP_AUDIT_SCANS_TOTAL.labels(
            status="failed", reason="unsupported_method", method=scan_method
        ).inc()
        return {
            "success": False,
            "reason": error_msg,
            "vulnerabilities_found": False,
            "vulnerability_count": 0,
            "vulnerabilities": [],
            "raw_output": "",
            "target_path": target_path,
            "scan_id": scan_id,
        }

    # --- Check cache before running scan ---
    cache_key = _build_cache_key(cache_payload)
    cached_result = await _get_cached_result(cache_key)
    if cached_result:
        PIP_AUDIT_SCANS_TOTAL.labels(
            status="success", reason="cached", method=scan_method
        ).inc()
        cached_result.setdefault("scan_id", scan_id)
        return cached_result

    logger.info(
        f"[{scan_id}] Running pip-audit scan (Method: {scan_method}, Mode: {invocation_mode}, Version: {version_str}, CWD: {cwd}): {' '.join(cmd)}"
    )

    stdout_data = ""
    stderr_data = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=PIP_AUDIT_CONFIG.default_timeout_seconds
        )
        stdout_data = stdout_bytes.decode(errors="replace")
        stderr_data = stderr_bytes.decode(errors="replace")

        # pip-audit returns 0 (no vulns) or 1 (vulns found) for successful runs
        # >1 considered errors
        scan_process_success = proc.returncode in (0, 1)
        parsed_vulnerabilities: List[Dict[str, Any]] = []

        try:
            # Prefer strict JSON parse first
            try:
                audit_json = json.loads(stdout_data)
            except json.JSONDecodeError:
                # Fallback: attempt to locate a JSON object
                json_start = stdout_data.find("{")
                json_end = stdout_data.rfind("}")
                if json_start != -1 and json_end != -1 and json_end > json_start:
                    audit_json = json.loads(stdout_data[json_start : json_end + 1])
                else:
                    raise

            for vuln_entry in audit_json.get("vulnerabilities", []):
                pkg = vuln_entry.get("package", {}).get("name")
                version = vuln_entry.get("package", {}).get("version")
                vuln = vuln_entry.get("vuln", {})
                severity = vuln.get("severity") or _parse_severity_from_description(
                    vuln.get("description", "")
                )
                parsed_vulnerabilities.append(
                    {
                        "package": pkg,
                        "version": version,
                        "vulnerability_id": vuln.get("id"),
                        "description": vuln.get("description"),
                        "severity": severity,
                        "aliases": vuln.get("aliases", []),
                        "fix_versions": vuln.get("fix_versions", []),
                        "source_tool": "pip-audit",
                    }
                )
        except json.JSONDecodeError as e:
            logger.error(
                f"[{scan_id}] Failed to parse pip-audit JSON output: {e}", exc_info=True
            )
            stderr_data += "\nJSON_PARSE_ERROR: " + str(e)
            # Consider JSON parse failure transient (retriable) if tenacity is enabled
            raise TransientScanError("Failed to parse JSON output from pip-audit")
        except Exception as e:
            logger.error(
                f"[{scan_id}] Error processing pip-audit output: {e}", exc_info=True
            )
            stderr_data += "\nPROCESSING_ERROR: " + str(e)
            scan_process_success = False

        vulnerabilities_found = len(parsed_vulnerabilities) > 0

        if not scan_process_success:
            # Retry transient errors where appropriate (heuristic)
            if tenacity_available and any(
                s in (stderr_data or "").lower()
                for s in [
                    "temporary failure",
                    "timed out",
                    "connection reset",
                    "network",
                    "rate limit",
                    "retry later",
                ]
            ):
                raise TransientScanError("Transient pip-audit failure detected")

        trimmed_out, trimmed_err = _trim_and_optionally_scrub(stdout_data, stderr_data)
        result = {
            "success": scan_process_success,
            "reason": (
                "Scan completed." if scan_process_success else "Scan process failed."
            ),
            "vulnerabilities_found": vulnerabilities_found,
            "vulnerability_count": len(parsed_vulnerabilities),
            "vulnerabilities": parsed_vulnerabilities,
            "raw_output": f"STDOUT:\n{trimmed_out}\nSTDERR:\n{trimmed_err}",
            "scan_method": scan_method,
            "target_path": target_path,
            "error": trimmed_err if not scan_process_success else None,
            "scan_id": scan_id,
            "pip_audit_version": version_str,
        }

        PIP_AUDIT_LATENCY_SECONDS.labels(method=scan_method).observe(
            time.monotonic() - start_time
        )
        if result["success"]:
            PIP_AUDIT_SCANS_TOTAL.labels(
                status="success", reason="completed", method=scan_method
            ).inc()
            if vulnerabilities_found:
                logger.warning(
                    f"[{scan_id}] pip-audit scan completed: {len(parsed_vulnerabilities)} vulnerabilities found."
                )
                for vuln in parsed_vulnerabilities:
                    detected_severity = vuln.get("severity", "UNKNOWN")
                    PIP_AUDIT_VULNERABILITIES_DETECTED.labels(
                        severity=detected_severity
                    ).inc()
            else:
                logger.info(
                    f"[{scan_id}] pip-audit scan completed: No vulnerabilities found."
                )
        else:
            PIP_AUDIT_SCANS_TOTAL.labels(
                status="failed", reason="process_error", method=scan_method
            ).inc()
            PIP_AUDIT_ERRORS_TOTAL.labels(error_type="cli_execution_failure").inc()

        await _audit_event(
            "pip_audit_scan_completed",
            {
                "scan_id": scan_id,
                "success": result["success"],
                "vulnerabilities_found": result["vulnerabilities_found"],
                "vulnerability_count": result["vulnerability_count"],
                "scan_method": scan_method,
                "target_path": target_path,
                "reason": result["reason"],
                "error": result["error"],
                "audit_summary": [
                    {"id": v.get("vulnerability_id"), "severity": v.get("severity")}
                    for v in parsed_vulnerabilities[:100]
                ],  # cap summary
                "pip_audit_version": version_str,
            },
        )

        # Cache result if successful
        if result["success"]:
            await _cache_scan_result(cache_key, result)

        return result

    except (FileNotFoundError, subprocess.SubprocessError) as e:
        error_msg = f"pip-audit CLI invocation failed: {e}"
        logger.error(f"[{scan_id}] {error_msg}", exc_info=True)
        PIP_AUDIT_ERRORS_TOTAL.labels(error_type="subprocess_error").inc()
        PIP_AUDIT_SCANS_TOTAL.labels(
            status="failed", reason="subprocess_error", method=scan_method
        ).inc()
        return {
            "success": False,
            "reason": error_msg,
            "vulnerabilities_found": False,
            "vulnerability_count": 0,
            "vulnerabilities": [],
            "raw_output": str(e),
            "error": error_msg,
            "target_path": target_path,
            "scan_id": scan_id,
        }
    except asyncio.TimeoutError:
        error_msg = f"pip-audit CLI timed out after {PIP_AUDIT_CONFIG.default_timeout_seconds} seconds."
        logger.error(f"[{scan_id}] {error_msg}")
        PIP_AUDIT_ERRORS_TOTAL.labels(error_type="timeout").inc()
        PIP_AUDIT_SCANS_TOTAL.labels(
            status="failed", reason="timeout", method=scan_method
        ).inc()
        # Let tenacity retry on TimeoutError (decorator covers it)
        raise
    except TransientScanError as e:
        logger.warning(
            f"[{scan_id}] Transient scan error: {e}. Will retry if configured."
        )
        PIP_AUDIT_ERRORS_TOTAL.labels(error_type="transient").inc()
        # Re-raise to trigger tenacity retry
        raise
    except Exception as e:
        error_msg = f"An unexpected error occurred during pip-audit scan: {e}"
        logger.error(f"[{scan_id}] {error_msg}", exc_info=True)
        PIP_AUDIT_ERRORS_TOTAL.labels(error_type="unexpected_exception").inc()
        PIP_AUDIT_SCANS_TOTAL.labels(
            status="failed", reason="unexpected_error", method=scan_method
        ).inc()
        return {
            "success": False,
            "reason": error_msg,
            "vulnerabilities_found": False,
            "vulnerability_count": 0,
            "vulnerabilities": [],
            "raw_output": str(e),
            "error": error_msg,
            "target_path": target_path,
            "scan_id": scan_id,
        }


def register_plugin_entrypoints(register_func: Callable):
    logger.info("Registering PipAuditPlugin entrypoints...")
    # Align name with PLUGIN_MANIFEST entry point key
    register_func(
        name="scan_dependencies",
        executor_func=scan_dependencies,
        capabilities=["dependency_vulnerability_scanning", "security_auditing"],
    )


# --- Standalone CLI and API Wrapper for Direct Testing ---
if __name__ == "__main__":
    import argparse

    try:
        import uvicorn
        from fastapi import FastAPI, HTTPException, status
        from pydantic import BaseModel as _ApiBaseModel

        FASTAPI_AVAILABLE_FOR_MAIN = True
    except ImportError:
        FASTAPI_AVAILABLE_FOR_MAIN = False

    parser = argparse.ArgumentParser(
        description="Run pip-audit plugin in standalone mode."
    )
    parser.add_argument("--api", action="store_true", help="Run as FastAPI API server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host for API server.")
    parser.add_argument("--port", type=int, default=8000, help="Port for API server.")
    parser.add_argument("--scan", action="store_true", help="Run a direct CLI scan.")
    parser.add_argument(
        "--target-path", help="Path to project or requirements.txt (for --scan)."
    )
    parser.add_argument(
        "--scan-method",
        default="installed",
        choices=["installed", "requirements"],
        help="Scan method (for --scan).",
    )
    parser.add_argument(
        "--pip-audit-arg",
        action="append",
        help="Additional pip-audit CLI arguments (e.g., --pip-audit-arg='--verbose').",
    )
    parser.add_argument(
        "--python-executable",
        help="Python executable to use for pip-audit (e.g., /usr/bin/python3).",
    )

    args = parser.parse_args()

    if args.api:
        if not FASTAPI_AVAILABLE_FOR_MAIN:
            print("FastAPI not available. Cannot run API server.", file=sys.stderr)
            sys.exit(1)
        app = FastAPI(title="PipAudit Plugin API")

        class ScanRequest(_ApiBaseModel):  # type: ignore
            target_path: Optional[str] = None
            scan_method: str = PIP_AUDIT_CONFIG.default_scan_method
            pip_audit_args: Optional[List[str]] = None
            python_executable: Optional[str] = None

        @app.post("/scan_dependencies", response_model=Dict[str, Any])
        async def scan_endpoint(request: ScanRequest):
            try:
                result = await scan_dependencies(
                    target_path=request.target_path,
                    scan_method=request.scan_method,
                    pip_audit_args=request.pip_audit_args,
                    python_executable=request.python_executable,
                )
                return result
            except Exception as e:
                logger.error(
                    f"API call to scan_dependencies failed: {e}", exc_info=True
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
                )

        @app.get("/health", response_model=Dict[str, Any])
        async def health_endpoint():
            # Module-mode validation optional; here we exercise CLI health by default
            return await plugin_health()

        print(f"Starting PipAudit Plugin API server on {args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
    elif args.scan:

        async def _run_cli_scan():
            print("\n--- Running PipAudit Plugin CLI Scan ---")
            extra_args: List[str] = []
            if args.pip_audit_arg:
                for arg_str in args.pip_audit_arg:
                    # Split by space but respect simple quotes stripping
                    split_args = [a.strip().strip("'\"") for a in arg_str.split(" ")]
                    extra_args.extend([a for a in split_args if a])
            result = await scan_dependencies(
                target_path=args.target_path,
                scan_method=args.scan_method,
                pip_audit_args=extra_args,
                python_executable=args.python_executable,
            )
            print("\n--- Scan Result ---")
            print(json.dumps(result, indent=2))
            if not result.get("success"):
                sys.exit(1)

        asyncio.run(_run_cli_scan())
    else:
        parser.print_help()
        print(
            "\nTo run tests, execute `python -m unittest` in the project root after installation."
        )
