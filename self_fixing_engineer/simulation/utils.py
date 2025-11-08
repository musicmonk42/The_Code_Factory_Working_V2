# The file has been updated to incorporate the logic and features from the second utils.py file,
# located in the plugins directory. This merge enhances the original file with advanced
# provenance logging, robust error handling, and a more comprehensive summary function.
# The new version of the file, simulation/utils.py, now serves as the canonical,
# production-ready utility module for the entire project.

"""
simulation.utils - A canonical collection of enterprise-grade utility functions for file and data operations.
This module provides robust and scalable functions for tasks such as file hashing, file searching,
and data serialization, with a focus on comprehensive error handling, logging, security, performance,
provenance, and plug-in support.
"""
from __future__ import annotations

import hashlib
import json
import difflib
import logging
import configparser
import os
import stat
import tempfile
import shutil
import traceback
import asyncio
import threading
import re
from pathlib import Path
from typing import Any, Dict, List, Union, Callable, Optional, Tuple
from functools import lru_cache
from prometheus_client import Counter, Histogram, Gauge, REGISTRY
from datetime import datetime
from packaging.version import parse as parse_version
from contextlib import contextmanager

# --- Logging ---
logger = logging.getLogger("simulation.utils")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

# --- Conditional Imports for Enhancements ---
try:
    from pydantic import BaseModel, Field, ValidationError
    pydantic_available = True
except ImportError:
    pydantic_available = False

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    tenacity_available = True
    logger.info("Tenacity library is available, retries are enabled.")
except ImportError:
    tenacity_available = False
    logger.warning("Tenacity library not found. Retries are disabled for retryable functions.")

    def retry(*args, **kwargs):
        def wrap(f): return f
        return wrap

    def stop_after_attempt(x): return None
    def wait_exponential(*args, **kwargs): return None
    def retry_if_exception_type(x): return None

try:
    from detect_secrets.core import SecretsCollection
    from detect_secrets.settings import transient_settings
    detect_secrets_available = True
except ImportError:
    detect_secrets_available = False

try:
    import aiofiles
    aiofiles_available = True
except ImportError:
    aiofiles_available = False

try:
    from simulation.audit_log import AuditLogger
    AUDIT_LOGGER_AVAILABLE = True
except ImportError:
    AUDIT_LOGGER_AVAILABLE = False


# --- Configuration and Core Versioning ---
config = configparser.ConfigParser()
config_file = Path(os.getenv("UTILS_CONFIG_FILE", "config.ini"))
if config_file.exists():
    mode = config_file.stat().st_mode
    if mode & stat.S_IWOTH or mode & stat.S_IWGRP:
        logger.warning(f"Config file {config_file} has insecure permissions (writable by others or group).")
    config.read(config_file)
try:
    DEFAULT_CHUNK_SIZE = int(os.getenv("UTILS_CHUNK_SIZE", config.getint("utils", "chunk_size", fallback=8192)))
    DEFAULT_WORKERS = int(os.getenv("UTILS_WORKERS", config.getint("utils", "workers", fallback=4)))
    DEFAULT_DIFF_CHUNK = int(os.getenv("UTILS_DIFF_CHUNK", config.getint("utils", "diff_chunk_size", fallback=1000)))
    if DEFAULT_CHUNK_SIZE <= 0 or DEFAULT_WORKERS <= 0 or DEFAULT_DIFF_CHUNK <= 0:
        raise ValueError("Configuration values must be positive integers")
except ValueError as e:
    logger.error(f"Invalid configuration: {e}")
    raise

if pydantic_available:
    class UtilsConfig(BaseModel):
        core_sim_runner_version: str = Field(default="1.1.0")
        results_dir: str = Field(default="./simulation_results")
        provenance_log_path: str = Field(default="provenance.log")
else:
    class UtilsConfig:
        def __init__(self):
            self.core_sim_runner_version = "1.1.0"
            self.results_dir = "./simulation_results"
            self.provenance_log_path = "provenance.log"

def _load_config() -> UtilsConfig:
    config_file_path = Path(__file__).parent / "configs" / "utils_config.json"
    config_dict: Dict[str, Any] = {}
    if config_file_path.exists():
        try:
            with open(config_file_path, "r", encoding="utf-8") as f:
                config_dict = json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(f"Could not load config file {config_file_path}: {e}. Using environment variables and defaults.")

    defaults = UtilsConfig()
    default_keys = list(getattr(defaults, "model_dump", lambda: defaults.__dict__)().keys())

    for key in default_keys:
        env_var = os.getenv(f"SFE_{key.upper()}")
        if env_var is not None:
            config_dict[key] = env_var

    if pydantic_available:
        try:
            if hasattr(UtilsConfig, "model_validate"):
                return UtilsConfig.model_validate(config_dict)
            return UtilsConfig.parse_obj(config_dict)
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}. Using defaults.")
            return UtilsConfig()
    else:
        defaults.__dict__.update(config_dict)
        return defaults

CONFIG = _load_config()
CORE_SIM_RUNNER_VERSION = os.environ.get("CORE_SIM_RUNNER_VERSION", getattr(CONFIG, "core_sim_runner_version"))
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = (BASE_DIR / getattr(CONFIG, "results_dir")).resolve()
os.makedirs(RESULTS_DIR, exist_ok=True)
SAFE_BASES = [Path.cwd().resolve(), RESULTS_DIR, Path(tempfile.gettempdir()).resolve()]


# --- Metrics ---
_metrics_lock = threading.Lock()

def _canonical_metric_name(name: str) -> str:
    for suffix in ("_total", "_created"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name

def _existing_metric_from_registry(name: str, registry=REGISTRY):
    base = _canonical_metric_name(name)
    try:
        names_map = getattr(registry, "_names_to_collectors", None)
        if isinstance(names_map, dict):
            collectors = names_map.get(base)
            if collectors:
                return next(iter(collectors))
    except Exception:
        pass
    try:
        c2n = getattr(registry, "_collector_to_names", None)
        if isinstance(c2n, dict):
            for collector, names in c2n.items():
                try:
                    if base in names:
                        return collector
                except Exception:
                    continue
    except Exception:
        pass
    return None

def get_or_create_metric(metric_type, name, documentation, labelnames=(), buckets=None, registry=REGISTRY):
    cname = _canonical_metric_name(name)
    with _metrics_lock:
        existing = _existing_metric_from_registry(cname, registry=registry)
        if existing is not None:
            return existing
        try:
            if metric_type is Counter:
                return Counter(cname, documentation, labelnames=labelnames, registry=registry)
            elif metric_type is Histogram or metric_type.__name__ == 'Histogram':
                if not hasattr(metric_type, 'DEFAULT_BUCKETS'):
                    buckets = buckets or (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0, 15.0)
                else:
                    buckets = buckets or metric_type.DEFAULT_BUCKETS
                return Histogram(
                    cname, documentation, labelnames=labelnames,
                    buckets=buckets, registry=registry
                )
            elif metric_type is Gauge:
                return Gauge(cname, documentation, labelnames=labelnames, registry=registry)
            else:
                raise ValueError(f"Unsupported metric type: {metric_type}")
        except ValueError:
            existing = _existing_metric_from_registry(cname, registry=registry)
            if existing is not None:
                return existing
            raise

hash_counter = get_or_create_metric(Counter, "utils_hash_file", "File hash computations")
diff_counter = get_or_create_metric(Counter, "utils_file_diff", "File diff computations")
save_counter = get_or_create_metric(Counter, "utils_save_result", "Simulation results saved")

FILE_OPERATIONS = get_or_create_metric(Counter, "utils_file_operations", "Total file operations", ["operation", "status"])
PROVENANCE_LOGS = get_or_create_metric(Counter, "utils_provenance_logs", "Total provenance log entries", ["status"])

# --- Security and Sanitization ---
def sanitize_path(path: Union[str, Path], base_dir: Optional[Union[str, Path]] = None) -> Path:
    raw = Path(path)
    if base_dir and not raw.is_absolute():
        p = (Path(base_dir) / raw).resolve()
    else:
        p = raw.resolve()
    if base_dir:
        base = Path(base_dir).resolve()
        try:
            if p.is_relative_to(base):
                return p
        except AttributeError:
            if str(p).startswith(str(base) + os.sep):
                return p
        logger.error(f"Path outside allowed base: {p} (base={base})")
        raise ValueError(f"Path '{path}' escapes base_dir '{base_dir}'.")
    else:
        allowed_bases = [b.resolve() for b in SAFE_BASES]
        for root in allowed_bases:
            try:
                if p.is_relative_to(root):
                    return p
            except AttributeError:
                if str(p).startswith(str(root) + os.sep):
                    return p
        logger.error(f"Path outside allowed safe roots: {p}")
        raise ValueError(f"Path '{path}' is not allowed; it is outside the safe roots.")

def validate_safe_path(path: Union[str, Path], base_dir: Union[str, Path]) -> Path:
    base = Path(base_dir).resolve()
    raw = Path(path)
    p = (base / raw).resolve() if not raw.is_absolute() else raw.resolve()
    try:
        inside = p.is_relative_to(base)
    except AttributeError:
        inside = str(p).startswith(str(base) + os.sep)
    if not inside:
        raise ValueError(f"File path '{path}' is not within the allowed base directory '{base_dir}'.")
    return p

def redact_sensitive(text: str) -> str:
    if not isinstance(text, str):
        return text
    patterns = {
        "api_key": r"(?:sk|rk|pk)_[A-Za-z0-9]{10,}",
        "password": r"(password|pass|secret|token)\s*[:=]\s*([^\n\r,;&\s]+)",
        "credit_card": r"\b(?:\d{4}[- ]?){3}\d{4}\b",
        "jwt": r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
    }
    redacted = text
    for key, pattern in patterns.items():
        redacted = re.sub(pattern, f"[{key.upper()}_SCRUBBED]", redacted, flags=re.IGNORECASE)
    return redacted

def _scrub_secrets(data: Union[Dict, List, str]) -> Union[Dict, List, str]:
    try:
        if isinstance(data, str):
            text = redact_sensitive(data)
            if detect_secrets_available:
                try:
                    sc = SecretsCollection()
                    with transient_settings():
                        sc.scan_string(text)
                    for secret in sc:
                        val = getattr(secret, "secret_value", None)
                        if val:
                            text = text.replace(val, "[REDACTED]")
                except Exception:
                    pass
            return text
        if isinstance(data, dict):
            return {k: _scrub_secrets(v) for k, v in data.items()}
        if isinstance(data, list):
            return [_scrub_secrets(item) for item in data]
        return data
    except Exception as e:
        logger.error(f"Failed to scrub secrets: {e}. Returning original data.")
        return data

def _fire_and_forget(coro):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()

# --- Provenance and Auditing ---
class ScalableProvenanceLogger:
    def __init__(self, storage_path: str = None):
        self.provenance_file = Path(storage_path or os.environ.get("PROVENANCE_LOG_PATH", getattr(CONFIG, "provenance_log_path"))).resolve()
        self._lock = threading.Lock()
        self._logger = logging.getLogger("simulation.provenance")
        self.central_audit_logger: Optional['AuditLogger'] = None
        self._last_audit_entry_hash: Optional[str] = None
        if AUDIT_LOGGER_AVAILABLE:
            try:
                self.central_audit_logger = AuditLogger.from_environment()
                self._logger.info("Delegating provenance logging to central AuditLogger.")
            except Exception as e:
                self._logger.warning(f"Could not initialize central AuditLogger: {e}. Falling back to local file logging for provenance.")
                self.central_audit_logger = None

    def log(self, event: Dict[str, Any]):
        event_with_time = dict(event)
        event_with_time["timestamp"] = datetime.utcnow().isoformat()
        scrubbed_event = _scrub_secrets(event_with_time)
        event_json_for_hash = json.dumps(scrubbed_event, sort_keys=True, default=str, ensure_ascii=False)
        current_event_hash = hashlib.sha256(
            (self._last_audit_entry_hash or "0" * 64).encode("utf-8") + event_json_for_hash.encode("utf-8")
        ).hexdigest()
        scrubbed_event["prev_hash"] = self._last_audit_entry_hash
        scrubbed_event["chain_hash"] = current_event_hash
        self._last_audit_entry_hash = current_event_hash
        if self.central_audit_logger:
            try:
                parts = str(scrubbed_event.get("event_type", "unknown_event")).split(".")
                audit_kind = parts[0]
                audit_name = ".".join(parts[1:]) if len(parts) > 1 else ""
                audit_details = scrubbed_event.get("payload", scrubbed_event.get("details", {}))
                audit_agent_id = (audit_details.get("agent_id") if isinstance(audit_details, dict) else None) or "simulation_utils"
                _fire_and_forget(
                    self.central_audit_logger.add_entry(
                        kind=audit_kind,
                        name=audit_name,
                        detail=audit_details,
                        agent_id=audit_agent_id,
                        log_hash=current_event_hash,
                        prev_log_hash=scrubbed_event["prev_hash"],
                    )
                )
                self._logger.debug(f"Delegated provenance event '{scrubbed_event.get('event_type')}' to central AuditLogger.")
                PROVENANCE_LOGS.labels(status='delegated').inc()
                return
            except Exception as e:
                self._logger.error(
                    f"Failed to delegate provenance event to central AuditLogger: {e}. Falling back to local file.",
                    exc_info=True
                )
                PROVENANCE_LOGS.labels(status='delegation_failed').inc()
        line = json.dumps(scrubbed_event, ensure_ascii=False, default=str)
        try:
            with self._lock:
                self.provenance_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.provenance_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            self._logger.info(f"Provenance logged to local file: {scrubbed_event.get('event_type')}")
            FILE_OPERATIONS.labels(operation='save_provenance', status='success').inc()
        except Exception as e:
            self._logger.error(f"Provenance log failure (local file): {e}")
            FILE_OPERATIONS.labels(operation='save_provenance', status='failure').inc()

provenance_logger = ScalableProvenanceLogger()

# --- Core File and Data Operations ---
def _hash_key(path: str) -> Tuple[int, int]:
    st = Path(path).stat()
    return (st.st_mtime_ns, st.st_size)

@lru_cache(maxsize=128)
def _compute_hash_cached(path: str, algo: str, chunk_size: int, mtime_ns: int, size: int) -> str:
    p = Path(path)
    try:
        h = hashlib.new(algo)
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()
    except PermissionError:
        logger.error(f"Permission denied when accessing file: {p}")
        raise
    except OSError as e:
        logger.error(f"An OS error occurred while hashing {p}: {e}")
        raise

def _compute_hash(path: str, algo: str, chunk_size: int) -> str:
    mtime_ns, size = _hash_key(path)
    return _compute_hash_cached(path, algo, chunk_size, mtime_ns, size)

def hash_file(
    path: Union[str, Path],
    algos: Union[str, List[str]] = "sha256",
    chunk_size: Optional[int] = None
) -> Union[str, Dict[str, str]]:
    hash_counter.inc()
    if chunk_size is None:
        chunk_size = DEFAULT_CHUNK_SIZE
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        logger.error(f"Invalid chunk_size: {chunk_size}")
        raise ValueError("chunk_size must be a positive integer")
    p = sanitize_path(path)
    if not p.is_file():
        logger.error(f"File not found: {p}")
        raise FileNotFoundError(f"File not found: {p}")
    algorithms = [algos] if isinstance(algos, str) else list(algos)
    for algo in algorithms:
        try:
            hashlib.new(algo)
        except ValueError as e:
            logger.error(f"Invalid hashing algorithm: {algo}")
            raise ValueError(f"Invalid hashing algorithm: {algo}") from e
    path_str = str(p)
    if isinstance(algos, str):
        hash_value = _compute_hash(path_str, algos, chunk_size)
        logger.info(f"Computed {algos} hash for {p}.")
        return hash_value
    result = {algo: _compute_hash(path_str, algo, chunk_size) for algo in algorithms}
    logger.info(f"Computed multiple hashes for {p}: {list(result.keys())}")
    return result

def find_files_by_pattern(root: Union[str, Path], pattern: str) -> List[Path]:
    if not isinstance(pattern, str) or not pattern.strip():
        logger.error(f"Invalid glob pattern: '{pattern}'")
        raise ValueError("Glob pattern must be a non-empty string")
    root_path = sanitize_path(root)
    if not root_path.is_dir():
        logger.error(f"Root path is not a directory: {root_path}")
        raise NotADirectoryError(f"Root path is not a directory: {root_path}")
    logger.info(f"Starting file search in {root_path} for pattern '{pattern}'.")
    found_files = list(root_path.rglob(pattern))
    seen, unique = set(), []
    for p in found_files:
        if p.is_file() and p not in seen:
            seen.add(p)
            unique.append(p)
    logger.info(f"Found {len(unique)} files matching the pattern '{pattern}'.")
    return unique

def load_artifact(path: Union[str, Path], max_bytes: int = 100 * 1024 * 1024) -> Optional[str]:
    p = sanitize_path(path)
    if not p.exists():
        logger.error(f"File not found: {p}")
        FILE_OPERATIONS.labels(operation='load', status='not_found').inc()
        return None
    try:
        size = p.stat().st_size
        if size > max_bytes:
            logger.warning(f"File {p} too large to load ({size} bytes > {max_bytes} bytes limit).")
            FILE_OPERATIONS.labels(operation='load', status='too_large').inc()
            return None
        with p.open("r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        FILE_OPERATIONS.labels(operation='load', status='success').inc()
        return content
    except (IOError, PermissionError) as e:
        logger.error(f"Error reading file {p}: {e}")
        FILE_OPERATIONS.labels(operation='load', status='io_error').inc()
        return None

def print_file_diff(
    a: Union[str, Path],
    b: Union[str, Path],
    diff_format: str = "unified",
    custom_formatter: Optional[Callable[[List[str], List[str], str, str], str]] = None
) -> str:
    diff_counter.inc()
    a_p, b_p = sanitize_path(a), sanitize_path(b)
    if not a_p.is_file() or not b_p.is_file():
        raise FileNotFoundError(f"Missing file: {a_p if not a_p.is_file() else b_p}")
    try:
        with a_p.open("r", encoding="utf-8", errors="replace") as fa:
            a_lines = fa.readlines()
        with b_p.open("r", encoding="utf-8", errors="replace") as fb:
            b_lines = fb.readlines()
    except PermissionError:
        logger.error(f"Permission denied when reading file: {a_p} or {b_p}")
        raise
    except UnicodeDecodeError:
        logger.error(f"Encoding error when reading file: {a_p} or {b_p}")
        raise
    if custom_formatter:
        result = custom_formatter(a_lines, b_lines, str(a_p), str(b_p))
    elif diff_format == "unified":
        diff_iter = difflib.unified_diff(a_lines, b_lines, fromfile=str(a_p), tofile=str(b_p), n=3)
        result = "".join(diff_iter)
    elif diff_format == "context":
        diff_iter = difflib.context_diff(a_lines, b_lines, fromfile=str(a_p), tofile=str(b_p), n=3)
        result = "".join(diff_iter)
    else:
        logger.error(f"Unsupported diff format: {diff_format}")
        raise ValueError(f"Unsupported diff format: {diff_format}")
    logger.info(f"Successfully generated {diff_format} diff between {a_p} and {b_p}.")
    return result

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    retry=retry_if_exception_type((IOError, PermissionError)) if tenacity_available else None
)
async def save_sim_result(data: Dict[str, Any], out_path: Union[str, Path]) -> Path:
    save_counter.inc()
    try:
        json.dumps(data, default=str)
    except TypeError as e:
        logger.error(f"Data is not JSON-serializable: {e}")
        raise TypeError("Input data is not JSON-serializable") from e
    p = sanitize_path(out_path)
    provenance_logger.log({
        "event_type": "save_sim_result_attempt",
        "payload": {
            "file": str(p),
            "status": "attempting"
        }
    })
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if aiofiles_available:
            async with aiofiles.open(p, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        else:
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        logger.info(f"Successfully saved simulation result to {p}.")
        provenance_logger.log({
            "event_type": "save_sim_result",
            "payload": {
                "file": str(p),
                "status": "success",
                "file_hash": hash_file(p)
            }
        })
        FILE_OPERATIONS.labels(operation='save_result', status='success').inc()
        return p
    except Exception as e:
        logger.error(f"Error saving simulation results to {p}: {e}")
        provenance_logger.log({
            "event_type": "save_sim_result",
            "payload": {
                "file": str(p),
                "status": "failure",
                "error": str(e)
            }
        })
        FILE_OPERATIONS.labels(operation='save_result', status='error').inc()
        raise

# --- Comprehensive Summary Function ---
def summarize_result(result: Dict[str, Any], detail_level: str = "auto") -> Dict[str, Any]:
    if not isinstance(result, dict):
        logger.warning("Input to summarize_result is not a dictionary. Returning invalid status.")
        return {"status": "invalid", "ok": False, "detail": "result not dict"}
    status = result.get("status", "unknown")
    if "plugin_runs" in result and result["plugin_runs"]:
        plugin_runs = result["plugin_runs"]
        summary = {
            "overall_status": status,
            "ok": bool(result.get("ok", status == "ok")),
            "plugin_run_count": len(plugin_runs),
            "failed_plugins": sum(1 for p_run in plugin_runs if p_run.get("result", {}).get("status") in ["ERROR", "FAILURE"]),
            "plugin_findings": sum(len(p_run.get("result", {}).get("findings", [])) for p_run in plugin_runs)
        }
    else:
        tasks = result.get("tasks") or []
        if isinstance(tasks, dict):
            tasks = list(tasks.values())
        summary = {
            "status": status,
            "ok": bool(result.get("ok", status == "ok")),
            "task_count": len(tasks),
            "errors": result.get("errors", []),
        }
    if detail_level in ("high", "auto"):
        total_runs = len(result.get("runs", []))
        failed_runs = sum(1 for r in result.get("runs", []) if r.get("returncode") != 0)
        summary["total_runs"] = total_runs
        summary["failed_runs"] = failed_runs
        coverage_info = result.get("coverage", {})
        if coverage_info.get("coverage") is not None:
            summary["code_coverage"] = coverage_info['coverage']
        if result.get("rl_reward") is not None:
            summary["rl_reward"] = result["rl_reward"]
        sustainability_info = result.get("sustainability", {})
        if "energy_kwh" in sustainability_info:
            summary["energy_kwh"] = sustainability_info["energy_kwh"]
        if "carbon_kg_co2" in sustainability_info:
            summary["carbon_kg_co2"] = sustainability_info["carbon_kg_co2"]
    logger.debug(f"Summarized result: {summary}")
    return summary

# --- Plugin API and Helpers ---
class PluginAPI:
    def __init__(self, plugin_name: str):
        self._plugin_name = plugin_name
        self._logger = logging.getLogger(f"Plugin.{plugin_name}")
        self._temp_dirs: List[Path] = []

    def get_logger(self) -> logging.Logger:
        return self._logger

    def create_temp_dir(self, prefix: str = "plugin_temp_") -> Path:
        temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
        self._temp_dirs.append(temp_dir)
        self._logger.info(f"Created temporary directory for {self._plugin_name}: {temp_dir}")
        return temp_dir

    def cleanup_temp_dirs(self):
        for temp_dir in self._temp_dirs:
            try:
                if temp_dir.exists():
                    shutil.rmtree(temp_dir)
                    self._logger.info(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                self._logger.error(f"Error cleaning up temporary directory {temp_dir}: {e}")
        self._temp_dirs = []

    @contextmanager
    def temp_dir_context(self, prefix: str = "plugin_temp_"):
        temp_dir = self.create_temp_dir(prefix=prefix)
        try:
            yield temp_dir
        finally:
            self.cleanup_temp_dirs()

    def get_core_version(self) -> str:
        return CORE_SIM_RUNNER_VERSION

    def check_core_compatibility(self, min_version: str, max_version: Optional[str] = None) -> bool:
        try:
            current_core_parsed = parse_version(CORE_SIM_RUNNER_VERSION)
            min_ver_parsed = parse_version(min_version)
            if current_core_parsed < min_ver_parsed:
                self._logger.error(
                    f"Incompatible core version. Plugin requires >= {min_version}, "
                    f"but core is {CORE_SIM_RUNNER_VERSION}."
                )
                return False
            if max_version:
                max_ver_parsed = parse_version(max_version)
                if current_core_parsed > max_ver_parsed:
                    self._logger.warning(
                        f"Core version {CORE_SIM_RUNNER_VERSION} is newer than "
                        f"plugin's max supported version {max_version}. "
                        f"Potential compatibility issues might arise, proceed with caution."
                    )
            return True
        except Exception as e:
            self._logger.error(
                f"Error during core version compatibility check: {e}. "
                f"Core: {CORE_SIM_RUNNER_VERSION}, Min: {min_version}, Max: {max_version}",
                exc_info=True
            )
            return False

    def report_result(self, result_type: str, data: Dict[str, Any]):
        safe_payload = json.dumps(data, default=str)
        self._logger.info(f"Plugin '{self._plugin_name}' reported {result_type} result: {safe_payload}")
        provenance_logger.log({
            "event_type": "plugin_result",
            "payload": {
                "plugin": self._plugin_name,
                "type": result_type,
                "data": data
            }
        })

    def handle_error(self, message: str, exception: Optional[Exception] = None, fatal: bool = False) -> Dict[str, Any]:
        error_details = {"plugin_name": self._plugin_name, "message": message}
        if exception:
            error_details["exception_type"] = type(exception).__name__
            error_details["exception_message"] = str(exception)
            error_details["traceback"] = traceback.format_exc()
            self._logger.exception(f"Plugin error: {message}")
        else:
            self._logger.error(f"Plugin error: {message}")
        error_details["status"] = "ERROR"
        error_details["fatal"] = fatal
        self.report_result("error", error_details)
        return error_details

    def warn_sandbox_limitations(self, manifest: Dict[str, Any]):
        if os.getenv("DISABLE_SANDBOX_WARNING", "false").lower() == "true":
            return
        plugin_type = manifest.get("type", "unknown")
        sandbox = manifest.get("sandbox", {})
        if plugin_type == "python" and sandbox.get("enabled", False):
            self._logger.warning(
                "Sandboxing for Python plugins is limited and experimental. "
                "Consider using WASM or gRPC for better isolation. Enable/disable is for monitoring, not security."
            )

__all__ = [
    "hash_file",
    "find_files_by_pattern",
    "print_file_diff",
    "summarize_result",
    "save_sim_result",
    "sanitize_path",
    "PluginAPI",
    "provenance_logger",
    "load_artifact"
]

class SecretStr(str):
    """Simple string wrapper for sensitive data."""
    
    def __new__(cls, value):
        return str.__new__(cls, str(value))
    
    def __repr__(self):
        return "SecretStr('***')"
    
    def get_secret_value(self):
        return str(self)
