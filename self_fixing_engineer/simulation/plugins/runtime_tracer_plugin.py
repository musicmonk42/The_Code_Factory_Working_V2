# plugins/runtime_tracer_plugin.py

import asyncio
import json
import logging
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# --- Minimal plugin manifest for integration with PluginManager (AST-extracted) ---
PLUGIN_MANIFEST = {
    "name": "RuntimeTracerPlugin",
    "version": "1.1.0",
    "description": "Runtime tracer for dynamic-call detection and error trace capture with container sandboxing.",
    "type": "python",
    # For single-file python plugins, PluginManager expects entrypoint and health_check; we point both to plugin_health.
    "entrypoint": "plugin_health",
    "health_check": "plugin_health",
    "api_version": "v1",
    "author": "Self-Fixing Engineer Team",
    "capabilities": [
        "runtime_tracing",
        "dynamic_code_analysis",
        "error_trace_analysis",
        "behavioral_healing_insights",
    ],
    "permissions": ["process_execution", "filesystem_read"],
    "dependencies": [],
    "min_core_version": "0.0.0",
    "max_core_version": "9.9.9",
    "license": "MIT",
    "homepage": "https://www.self-fixing.engineer",
    "tags": ["runtime", "dynamic_analysis", "tracing", "healing", "behavioral"],
    "sandbox": {"enabled": True},
    "manifest_version": "2.0",
}

# --- Logger Setup (FIRST) ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Platform capabilities
HAS_FCNTL = False
if platform.system().lower().startswith(("linux", "darwin")):
    try:

        HAS_FCNTL = True
    except Exception:
        HAS_FCNTL = False

# --- Prometheus Metrics (Idempotent Definition) ---
try:
    from prometheus_client import REGISTRY, Counter, Histogram

    def _get_or_create_metric(
        metric_type: type,
        name: str,
        documentation: str,
        labelnames: Optional[Tuple[str, ...]] = None,
        buckets: Optional[Tuple[float, ...]] = None,
    ) -> Any:
        try:
            # best-effort reuse; guard private attr
            names_to_collectors = getattr(REGISTRY, "_names_to_collectors", {})
            if isinstance(names_to_collectors, dict) and name in names_to_collectors:
                return names_to_collectors[name]
        except Exception:
            pass
        labelnames = labelnames or ()
        if metric_type == Histogram:
            return metric_type(
                name,
                documentation,
                labelnames=labelnames,
                buckets=buckets or Histogram.DEFAULT_BUCKETS,
            )
        if metric_type == Counter:
            return metric_type(name, documentation, labelnames=labelnames)
        return metric_type(name, documentation, labelnames=labelnames)

except ImportError:

    class DummyMetric:  # No-op dummy metric
        def inc(self, amount: float = 1.0):
            pass

        def set(self, value: float):
            pass

        def observe(self, value: float):
            pass

        def labels(self, *args, **kwargs):
            return self

    def _get_or_create_metric(*args, **kwargs) -> Any:
        return DummyMetric()


# Metrics for this plugin (low cardinality)
TRACE_ANALYSIS_ATTEMPTS = _get_or_create_metric(
    Counter,
    "runtime_trace_analysis_attempts_total",
    "Total runtime trace analysis attempts",
)
TRACE_ANALYSIS_SUCCESS = _get_or_create_metric(
    Counter,
    "runtime_trace_analysis_success_total",
    "Total successful runtime trace analyses",
)
TRACE_ANALYSIS_ERRORS = _get_or_create_metric(
    Counter,
    "runtime_trace_analysis_errors_total",
    "Total errors during runtime trace analysis",
    ("error_type",),
)
TRACE_EXECUTION_LATENCY_SECONDS = _get_or_create_metric(
    Histogram,
    "runtime_trace_execution_latency_seconds",
    "Latency of target code execution under trace",
)
DYNAMIC_CALLS_DETECTED = _get_or_create_metric(
    Counter,
    "runtime_dynamic_calls_detected_total",
    "Total dynamic calls detected",
    ("call_type",),
)
RUNTIME_EXCEPTIONS_CAPTURED = _get_or_create_metric(
    Counter,
    "runtime_exceptions_captured_total",
    "Total runtime exceptions captured",
    ("exception_type",),
)
RUNTIME_TRACER_RUNS_TOTAL = _get_or_create_metric(
    Counter,
    "runtime_tracer_runs_total",
    "Total analyses by sandbox mode",
    ("sandbox_mode",),
)


# --- Utilities ---
def _str2bool(val: Union[str, bool, None], default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "t", "yes", "y", "on")


def _truncate(s: Optional[str], max_len: int) -> Optional[str]:
    if s is None:
        return None
    return s if len(s) <= max_len else (s[:max_len] + "...[truncated]")


def _deny_weakening_docker_args(extra_args: List[str]) -> None:
    """
    Deny extra args that weaken isolation unless explicitly allowed by TRACER_ALLOW_UNSAFE_DOCKER_ARGS=true.
    """
    allow_unsafe = _str2bool(
        os.getenv("TRACER_ALLOW_UNSAFE_DOCKER_ARGS", "false"), False
    )
    if allow_unsafe:
        return
    weakening_flags = {"--privileged", "--pid=host", "--network=host", "--ipc=host"}
    # flags with values to block if present
    forbidden_prefixes = ("--cap-add",)
    forbidden_security = ("apparmor=unconfined", "seccomp=unconfined")
    for arg in extra_args:
        if arg in weakening_flags:
            raise ValueError(
                f"Forbidden docker arg for security: {arg}. Set TRACER_ALLOW_UNSAFE_DOCKER_ARGS=true to override (NOT RECOMMENDED)."
            )
        if any(arg.startswith(pfx) for pfx in forbidden_prefixes):
            raise ValueError(f"Forbidden docker arg for security: {arg}.")
        if arg.startswith("--security-opt"):
            # Example: --security-opt apparmor=unconfined
            if any(tok in arg for tok in forbidden_security):
                raise ValueError(
                    f"Forbidden docker security option for security: {arg}."
                )


# --- Plugin-Specific Configuration ---
TRACER_CONFIG = {
    "log_dynamic_calls": _str2bool(os.getenv("TRACER_LOG_DYNAMIC_CALLS", "true"), True),
    # Fixed inverted default: now false by default unless "true"
    "log_all_function_calls": _str2bool(
        os.getenv("TRACER_LOG_ALL_FUNCTION_CALLS", "false"), False
    ),
    "max_trace_duration_seconds": int(os.getenv("TRACER_MAX_DURATION_SECONDS", "30")),
    "subprocess_timeout_buffer": int(
        os.getenv("TRACER_SUBPROCESS_TIMEOUT_BUFFER", "5")
    ),
    "redact_args_threshold": int(os.getenv("TRACER_REDACT_ARGS_THRESHOLD", "100")),
    "critical_functions_to_monitor": json.loads(
        os.getenv(
            "TRACER_CRITICAL_FUNCTIONS_TO_MONITOR",
            json.dumps(
                [
                    "eval",
                    "exec",
                    "__import__",
                    "os.system",
                    "subprocess.call",
                    "subprocess.run",
                ]
            ),
        )
    ),
    "base_temp_dir": os.getenv("TRACER_BASE_TEMP_DIR", None),
    "retain_temp_files": _str2bool(
        os.getenv("TRACER_RETAIN_TEMP_FILES", "false"), False
    ),
    "log_script_content_max_size_kb": int(
        os.getenv("TRACER_LOG_SCRIPT_CONTENT_MAX_SIZE_KB", "128")
    ),
    # Simpler, correct default: True unless explicitly set false
    "use_docker_sandbox": _str2bool(
        os.getenv("TRACER_USE_DOCKER_SANDBOX", "true"), True
    ),
    "docker_image": os.getenv("TRACER_DOCKER_IMAGE", "python:3.10-slim"),
    "docker_extra_args": os.getenv("TRACER_DOCKER_EXTRA_ARGS", ""),
    "trace_flush_interval_seconds": int(
        os.getenv("TRACER_FLUSH_INTERVAL_SECONDS", "1")
    ),
    "allow_unsafe_non_containerized_run": _str2bool(
        os.getenv("TRACER_ALLOW_UNSAFE", "false"), False
    ),
    # New: security/privacy and performance knobs
    "scrub_code_lines": _str2bool(os.getenv("TRACER_SCRUB_CODE_LINES", "true"), True),
    "max_trace_entry_len": int(os.getenv("TRACER_MAX_TRACE_ENTRY_LEN", "4096")),
    "max_buffer_entries": int(os.getenv("TRACER_MAX_BUFFER_ENTRIES", "10000")),
    # Observability and limits
    "max_trace_file_bytes": int(
        os.getenv("TRACER_MAX_TRACE_FILE_BYTES", str(5 * 1024 * 1024))
    ),  # 5MB
    # Container interpreter and readiness
    "container_python_cmd": os.getenv("TRACER_PY_CMD", "python"),
    "check_container_python": _str2bool(
        os.getenv("TRACER_CHECK_CONTAINER_PY", "true"), True
    ),
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


async def _audit_event(event_type: str, details: Dict[str, Any]):
    await _sfe_audit_logger.log(event_type, details)


# --- Health Check ---
async def plugin_health() -> Dict[str, Any]:
    status = "ok"
    details: List[str] = []

    # sys.settrace available
    if not hasattr(sys, "settrace"):
        status = "error"
        details.append(
            "sys.settrace is not available. Runtime tracing cannot function."
        )
    else:
        details.append("sys.settrace is available.")

    # Basic subprocess execution
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; print('ok')",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0 or stdout.decode().strip() != "ok":
            raise RuntimeError("Basic python subprocess execution failed.")
        details.append("Python subprocess execution confirmed.")
    except Exception as e:
        status = "error"
        details.append(
            f"Subprocess execution failed: {e}. Cannot run target code in isolation."
        )
        logger.error(details[-1], exc_info=True)

    # Container runtime check
    if TRACER_CONFIG["use_docker_sandbox"]:
        try:
            docker_cmd = shutil.which("docker") or shutil.which("podman")
            if not docker_cmd:
                raise FileNotFoundError("Neither 'docker' nor 'podman' command found.")
            result = subprocess.run(
                [docker_cmd, "info"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"{docker_cmd} info command failed: {result.stderr.strip()}"
                )
            details.append(
                f"Docker/Podman sandbox ({docker_cmd}) is available and functioning."
            )
            # Optional lightweight readiness: ensure python exists in image
            if TRACER_CONFIG["check_container_python"]:
                check_cmd = [
                    docker_cmd,
                    "run",
                    "--rm",
                    TRACER_CONFIG["docker_image"],
                    TRACER_CONFIG["container_python_cmd"],
                    "-V",
                ]
                res = subprocess.run(
                    check_cmd, capture_output=True, text=True, timeout=5, check=False
                )
                if res.returncode != 0:
                    details.append(
                        f"Warning: Container interpreter check failed: {res.stderr.strip() or res.stdout.strip()}"
                    )
                    logger.warning(details[-1])
                else:
                    details.append(
                        f"Container python present: {res.stdout.strip() or res.stderr.strip()}"
                    )
        except Exception as e:
            if TRACER_CONFIG["allow_unsafe_non_containerized_run"]:
                if status != "error":  # don't mask earlier fatal checks
                    status = "degraded"
                details.append(
                    f"Container runtime unavailable: {e}. Unsafe mode allowed; will run without containerization."
                )
                logger.warning(details[-1])
            else:
                status = "error"
                details.append(
                    f"Docker/Podman sandboxing is enabled but not functional: {e}. Ensure Docker/Podman is installed and running."
                )
                logger.error(details[-1], exc_info=True)
    else:
        details.append("Container sandboxing disabled by configuration.")

    # Check base_temp_dir writability
    if TRACER_CONFIG["base_temp_dir"]:
        try:
            os.makedirs(TRACER_CONFIG["base_temp_dir"], exist_ok=True)
            test_file = os.path.join(
                TRACER_CONFIG["base_temp_dir"], f"test_{uuid.uuid4().hex}"
            )
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            details.append(
                f"Configured base_temp_dir '{TRACER_CONFIG['base_temp_dir']}' is writable."
            )
        except Exception as e:
            status = "error"
            details.append(
                f"Configured base_temp_dir '{TRACER_CONFIG['base_temp_dir']}' is NOT writable: {e}. Temp file creation will fail."
            )
            logger.error(details[-1], exc_info=True)

    logger.info(f"Plugin health check: {status}")
    return {"status": status, "details": details}


# --- Subprocess runner template ---
# Note: enable/disable file locking via env var SFE_TRACER_USE_FCNTL; Windows will pass "false".
_SUBPROCESS_RUNNER_SCRIPT_TEMPLATE = r"""
import sys, os, json, time, traceback, threading, atexit, linecache, runpy

_USE_FCNTL = os.environ.get("SFE_TRACER_USE_FCNTL", "false").lower() == "true"
if _USE_FCNTL:
    try:
        import fcntl
    except Exception:
        _USE_FCNTL = False

_trace_log_buffer = []
_trace_lock = threading.Lock()
_subprocess_last_flush_time = time.monotonic()
_subprocess_output_log_file_path = os.environ.get("SFE_TRACER_OUTPUT_PATH")
_run_id = os.environ.get("SFE_TRACER_RUN_ID", "")
_max_file_bytes = int(os.environ.get("SFE_TRACER_MAX_TRACE_FILE_BYTES", str(5 * 1024 * 1024)))
_bytes_written = 0

_log_dynamic_calls = os.environ.get("TRACER_LOG_DYNAMIC_CALLS", "true").lower() == "true"
_log_all_function_calls = os.environ.get("TRACER_LOG_ALL_FUNCTION_CALLS", "false").lower() == "true"
_redact_args_threshold = int(os.environ.get("TRACER_REDACT_ARGS_THRESHOLD", "100"))
_critical_functions_to_monitor = json.loads(os.environ.get("TRACER_CRITICAL_FUNCTIONS_TO_MONITOR", "[]"))
_trace_flush_interval_seconds = int(os.environ.get("TRACER_FLUSH_INTERVAL_SECONDS", "1"))
_max_entry_len = int(os.environ.get("TRACER_MAX_TRACE_ENTRY_LEN", "4096"))
_max_buffer_entries = int(os.environ.get("TRACER_MAX_BUFFER_ENTRIES", "10000"))
_scrub_lines = os.environ.get("TRACER_SCRUB_CODE_LINES", "true").lower() == "true"

def _scrub(s: str) -> str:
    if not _scrub_lines or not s: return s
    # naive redaction of long tokens and obvious secrets-like strings
    import re
    s = re.sub(r"[A-Za-z0-9_\-]{24,}", "[REDACTED]", s)
    s = re.sub(r"(api[_-]?key|token|secret)\s*[:=]\s*[^\s'\";]+", r"\\1=[REDACTED]", s, flags=re.IGNORECASE)
    return s

def _safe_append(entry: dict):
    # cap message sizes defensively; stamp run_id
    entry["run_id"] = _run_id
    if "message" in entry and isinstance(entry["message"], str):
        entry["message"] = (entry["message"][:_max_entry_len] + "...[truncated]") if len(entry["message"]) > _max_entry_len else entry["message"]
    with _trace_lock:
        if len(_trace_log_buffer) < _max_buffer_entries:
            _trace_log_buffer.append(entry)
        else:
            if not _trace_log_buffer or _trace_log_buffer[-1].get("type") != "buffer_overflow":
                _trace_log_buffer.append({"type": "buffer_overflow", "timestamp": time.monotonic(), "dropped": 1, "run_id": _run_id})
            else:
                _trace_log_buffer[-1]["dropped"] += 1

def _subprocess_flush_trace_buffer():
    global _subprocess_last_flush_time, _bytes_written, _subprocess_output_log_file_path
    if not _subprocess_output_log_file_path:
        return
    try:
        # Acquire lock for consistent read of buffer
        with _trace_lock:
            if not _trace_log_buffer:
                return
            payload = _trace_log_buffer[:]
            _trace_log_buffer.clear()
        # Write out of lock to reduce contention
        with open(_subprocess_output_log_file_path, "a") as f:
            if _USE_FCNTL:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    # couldn't get lock; push back payload and skip this flush
                    with _trace_lock:
                        _trace_log_buffer[0:0] = payload
                    return
            # update bytes_written from file size
            try:
                _bytes_written = f.tell()
            except Exception:
                pass
            for entry in payload:
                line = json.dumps(entry, ensure_ascii=False) + "\\n"
                if _bytes_written + len(line.encode('utf-8', 'ignore')) > _max_file_bytes:
                    # mark limit reached and stop further writes
                    lim = {"type": "trace_limit_reached", "run_id": _run_id, "timestamp": time.monotonic(), "max_bytes": _max_file_bytes}
                    f.write(json.dumps(lim, ensure_ascii=False) + "\\n")
                    _bytes_written += len(json.dumps(lim, ensure_ascii=False).encode('utf-8')) + 1
                    _subprocess_output_log_file_path = ""  # disable further flushing
                    break
                f.write(line)
                _bytes_written += len(line.encode('utf-8', 'ignore'))
            f.flush()
            os.fsync(f.fileno())
            if _USE_FCNTL:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
        _subprocess_last_flush_time = time.monotonic()
    except Exception as e:
        # best-effort stderr
        print(f"ERROR: Subprocess trace buffer flush failed: {e}", file=sys.stderr)

def _subprocess_trace_calls(frame, event, arg):
    global _subprocess_last_flush_time
    if event == 'call':
        if _log_all_function_calls:
            try:
                fn = frame.f_code.co_filename
                if not fn.startswith('<') and "site-packages" not in fn and "dist-packages" not in fn:
                    _safe_append({
                        "type": "call",
                        "function": frame.f_code.co_name,
                        "file": os.path.basename(fn),
                        "line": frame.f_lineno,
                        "timestamp": time.monotonic(),
                    })
            except Exception as e:
                _safe_append({"type": "trace_error", "message": str(e), "timestamp": time.monotonic()})

    elif event == 'exception':
        exc_type, exc_value, tb = arg
        _safe_append({
            "type": "exception",
            "exception_type": exc_type.__name__,
            "message": str(exc_value)[:_max_entry_len],
            "file": os.path.basename(frame.f_code.co_filename),
            "line": frame.f_lineno,
            "function": frame.f_code.co_name,
            "timestamp": time.monotonic()
        })

    if event == 'line' and _log_dynamic_calls:
        try:
            fn = frame.f_code.co_filename
            code_line = linecache.getline(fn, frame.f_lineno)
        except Exception:
            code_line = ""
        if code_line:
            line_code_snippet = _scrub(code_line.strip())[:_redact_args_threshold]
            raw = code_line
            if any(crit in raw for crit in _critical_functions_to_monitor):
                dynamic_call_type = "critical_function_call"
                if "eval(" in raw or "exec(" in raw or "__import__(" in raw:
                    dynamic_call_type = "eval/exec/__import__"
                elif "os.system(" in raw or "subprocess.call(" in raw or "subprocess.run(" in raw:
                    dynamic_call_type = "os.system/subprocess"
                _safe_append({
                    "type": "dynamic_call_detected",
                    "call_type": dynamic_call_type,
                    "file": os.path.basename(fn),
                    "line": frame.f_lineno,
                    "code_line": line_code_snippet,
                    "timestamp": time.monotonic()
                })

    if _trace_flush_interval_seconds > 0 and time.monotonic() - _subprocess_last_flush_time > _trace_flush_interval_seconds:
        _subprocess_flush_trace_buffer()

    return _subprocess_trace_calls

def _main_subprocess_entrypoint():
    sys.settrace(_subprocess_trace_calls)
    threading.settrace(_subprocess_trace_calls)
    atexit.register(_subprocess_flush_trace_buffer)

    # Inputs via env
    target_code_path = os.environ.get("SFE_TRACER_TARGET_PATH")
    test_script_path = os.environ.get("SFE_TRACER_TEST_SCRIPT")
    execution_args = json.loads(os.environ.get("SFE_TRACER_EXEC_ARGS", "[]"))

    # Change CWD to target_code_path dir
    target_dir = os.path.dirname(target_code_path) or "."
    os.chdir(target_dir)
    if target_dir not in sys.path:
        sys.path.insert(0, target_dir)
    sys.argv = [target_code_path] + execution_args

    try:
        # Execute the target file as a script (__main__) so guarded blocks run
        runpy.run_path(target_code_path, run_name="__main__")

        # Execute test script in isolated globals (if provided)
        if test_script_path and test_script_path != "None":
            test_dir = os.path.dirname(test_script_path) or "."
            if test_dir not in sys.path:
                sys.path.insert(0, test_dir)
            with open(test_script_path, 'r', encoding='utf-8') as f_test:
                test_code = f_test.read()
            exec_globals = {"__name__": "__main__", "__file__": test_script_path}
            exec(test_code, exec_globals, None)
            print(f"Subprocess: Test script {test_script_path} executed.")

    except Exception as e:
        _safe_append({
            "type": "fatal_error",
            "message": f"Subprocess execution failed: {e}",
            "traceback": traceback.format_exc()[:_max_entry_len],
            "timestamp": time.monotonic()
        })
    finally:
        _subprocess_flush_trace_buffer()
        sys.settrace(None)
        threading.settrace(None)
        if any(entry.get("type") == "fatal_error" for entry in _trace_log_buffer):
            sys.exit(1)
        sys.exit(0)

if __name__ == '__main__':
    _main_subprocess_entrypoint()
"""


def _build_docker_run_command(
    docker_cmd: str,
    temp_script_dir: str,
    target_code_path: str,
    test_script_path: Optional[str],
    trace_log_file: str,
    execution_args: Optional[List[str]],
    run_id: str,
) -> Tuple[List[str], Dict[str, str]]:
    # Using --mount for portability (spaces supported inside the single mount-arg string)
    mounts: List[str] = []
    # target dir
    target_host_dir = os.path.dirname(os.path.abspath(target_code_path)) or "."
    # Reject comma-containing paths (docker --mount uses comma to separate options)
    for p in (
        target_host_dir,
        os.path.dirname(os.path.abspath(trace_log_file)) or ".",
        os.path.dirname(os.path.abspath(test_script_path)) if test_script_path else "",
    ):
        if p and ("," in p):
            raise RuntimeError(
                f"Host path contains a comma, which is incompatible with docker --mount: {p}. "
                f"Move the project to a path without commas or run in unsafe non-containerized mode for testing."
            )
    mounts += ["--mount", f"type=bind,src={target_host_dir},dst=/app_code,ro"]
    # log dir
    log_host_dir = os.path.dirname(os.path.abspath(trace_log_file)) or "."
    mounts += ["--mount", f"type=bind,src={log_host_dir},dst=/sfe_trace_output,rw"]
    # runner script dir
    mounts += ["--mount", f"type=bind,src={temp_script_dir},dst=/tmp_scripts,ro"]

    test_in_container = "None"
    if test_script_path:
        test_host_dir = os.path.dirname(os.path.abspath(test_script_path)) or "."
        if os.path.normpath(test_host_dir) != os.path.normpath(target_host_dir):
            mounts += ["--mount", f"type=bind,src={test_host_dir},dst=/app_test_dir,ro"]
            test_in_container = f"/app_test_dir/{os.path.basename(test_script_path)}"
        else:
            test_in_container = f"/app_code/{os.path.basename(test_script_path)}"

    # User mapping to preserve write permissions to mounted log dir
    user_flags: List[str] = []
    try:
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            user_flags = ["--user", f"{os.getuid()}:{os.getgid()}"]
    except Exception:
        user_flags = []

    extra_args = (
        shlex.split(TRACER_CONFIG["docker_extra_args"])
        if TRACER_CONFIG["docker_extra_args"]
        else []
    )
    _deny_weakening_docker_args(extra_args)

    # Environment for subprocess inside container
    env_vars_for_subprocess = {
        "TRACER_LOG_DYNAMIC_CALLS": str(TRACER_CONFIG["log_dynamic_calls"]).lower(),
        "TRACER_LOG_ALL_FUNCTION_CALLS": str(
            TRACER_CONFIG["log_all_function_calls"]
        ).lower(),
        "TRACER_REDACT_ARGS_THRESHOLD": str(TRACER_CONFIG["redact_args_threshold"]),
        "TRACER_CRITICAL_FUNCTIONS_TO_MONITOR": json.dumps(
            TRACER_CONFIG["critical_functions_to_monitor"]
        ),
        "TRACER_FLUSH_INTERVAL_SECONDS": str(
            TRACER_CONFIG["trace_flush_interval_seconds"]
        ),
        "TRACER_MAX_TRACE_ENTRY_LEN": str(TRACER_CONFIG["max_trace_entry_len"]),
        "TRACER_MAX_BUFFER_ENTRIES": str(TRACER_CONFIG["max_buffer_entries"]),
        "TRACER_SCRUB_CODE_LINES": str(TRACER_CONFIG["scrub_code_lines"]).lower(),
        "SFE_TRACER_TARGET_PATH": f"/app_code/{os.path.basename(target_code_path)}",
        "SFE_TRACER_OUTPUT_PATH": f"/sfe_trace_output/{os.path.basename(trace_log_file)}",
        "SFE_TRACER_TEST_SCRIPT": test_in_container,
        "SFE_TRACER_EXEC_ARGS": json.dumps(execution_args if execution_args else []),
        "SFE_TRACER_USE_FCNTL": "true" if HAS_FCNTL else "false",
        "SFE_TRACER_RUN_ID": run_id,
        "SFE_TRACER_MAX_TRACE_FILE_BYTES": str(TRACER_CONFIG["max_trace_file_bytes"]),
    }

    # Convert env vars to docker -e flags
    env_flags: List[str] = []
    for k, v in env_vars_for_subprocess.items():
        env_flags += ["-e", f"{k}={v}"]

    cmd: List[str] = [
        docker_cmd,
        "run",
        "--rm",
        "--cpus",
        "0.5",
        "--memory",
        "256m",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--network",
        "none",
        *user_flags,
        *env_flags,
        *extra_args,
        *mounts,
        TRACER_CONFIG["docker_image"],
        TRACER_CONFIG["container_python_cmd"],
        "-u",
        "/tmp_scripts/sfe_subprocess_runner.py",
    ]
    return cmd, env_vars_for_subprocess


async def _run_target_code_in_subprocess(
    target_code_path: str,
    trace_log_file: str,
    analysis_duration_seconds: int,
    test_script_path: Optional[str],
    execution_args: Optional[List[str]],
    run_id: str,
) -> Dict[str, Any]:
    """
    Spawns a new Python subprocess to run the target code under sys.settrace.
    Captures stdout/stderr and returns process metrics.
    """
    # Prepare runner script in temp dir
    temp_script_dir = tempfile.mkdtemp(
        prefix="sfe_tracer_runner_", dir=TRACER_CONFIG["base_temp_dir"]
    )
    temp_script_path = os.path.join(temp_script_dir, "sfe_subprocess_runner.py")
    with open(temp_script_path, "w", encoding="utf-8") as f:
        f.write(_SUBPROCESS_RUNNER_SCRIPT_TEMPLATE)

    command: List[str]
    env_vars_for_subprocess: Dict[str, str]

    if TRACER_CONFIG["use_docker_sandbox"]:
        docker_cmd = shutil.which("docker") or shutil.which("podman")
        if not docker_cmd:
            raise RuntimeError(
                "Docker/Podman command not found, but use_docker_sandbox is true."
            )
        command, env_vars_for_subprocess = _build_docker_run_command(
            docker_cmd=docker_cmd,
            temp_script_dir=temp_script_dir,
            target_code_path=target_code_path,
            test_script_path=test_script_path,
            trace_log_file=trace_log_file,
            execution_args=execution_args,
            run_id=run_id,
        )
    else:
        if not TRACER_CONFIG["allow_unsafe_non_containerized_run"]:
            logger.critical(
                "WARNING: OS-level container sandboxing is DISABLED. This is UNSAFE for untrusted code!"
            )
            raise RuntimeError(
                "Container sandboxing is required. Set TRACER_USE_DOCKER_SANDBOX=true or TRACER_ALLOW_UNSAFE=true."
            )
        else:
            logger.warning(
                "Running without OS-level sandboxing (TRACER_ALLOW_UNSAFE=true). NOT RECOMMENDED for untrusted code."
            )
        command = [sys.executable, "-u", temp_script_path]
        env_vars_for_subprocess = {
            "TRACER_LOG_DYNAMIC_CALLS": str(TRACER_CONFIG["log_dynamic_calls"]).lower(),
            "TRACER_LOG_ALL_FUNCTION_CALLS": str(
                TRACER_CONFIG["log_all_function_calls"]
            ).lower(),
            "TRACER_REDACT_ARGS_THRESHOLD": str(TRACER_CONFIG["redact_args_threshold"]),
            "TRACER_CRITICAL_FUNCTIONS_TO_MONITOR": json.dumps(
                TRACER_CONFIG["critical_functions_to_monitor"]
            ),
            "TRACER_FLUSH_INTERVAL_SECONDS": str(
                TRACER_CONFIG["trace_flush_interval_seconds"]
            ),
            "TRACER_MAX_TRACE_ENTRY_LEN": str(TRACER_CONFIG["max_trace_entry_len"]),
            "TRACER_MAX_BUFFER_ENTRIES": str(TRACER_CONFIG["max_buffer_entries"]),
            "TRACER_SCRUB_CODE_LINES": str(TRACER_CONFIG["scrub_code_lines"]).lower(),
            "SFE_TRACER_TARGET_PATH": os.path.abspath(target_code_path),
            "SFE_TRACER_OUTPUT_PATH": os.path.abspath(trace_log_file),
            "SFE_TRACER_TEST_SCRIPT": (
                os.path.abspath(test_script_path) if test_script_path else "None"
            ),
            "SFE_TRACER_EXEC_ARGS": json.dumps(
                execution_args if execution_args else []
            ),
            "SFE_TRACER_USE_FCNTL": "true" if HAS_FCNTL else "false",
            "SFE_TRACER_RUN_ID": run_id,
            "SFE_TRACER_MAX_TRACE_FILE_BYTES": str(
                TRACER_CONFIG["max_trace_file_bytes"]
            ),
        }

    proc = None
    stdout_data = ""
    stderr_data = ""
    start_time = time.monotonic()
    try:
        # Merge env (used for direct python runs; docker env is set via -e flags)
        env = os.environ.copy()
        env.update(env_vars_for_subprocess)

        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=analysis_duration_seconds
            + TRACER_CONFIG["subprocess_timeout_buffer"],
        )
        stdout_data = stdout_bytes.decode(errors="replace")
        stderr_data = stderr_bytes.decode(errors="replace")
        return_code = proc.returncode

    except asyncio.TimeoutError:
        logger.error(
            f"Subprocess for {target_code_path} timed out after {analysis_duration_seconds + TRACER_CONFIG['subprocess_timeout_buffer']}s."
        )
        if proc:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return_code = 1
        stderr_data += "\n--- Subprocess TIMEOUT ---\n"
    except Exception as e:
        logger.error(
            f"Error running subprocess for {target_code_path}: {e}", exc_info=True
        )
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        return_code = 1
        stderr_data += f"\n--- Subprocess ERROR: {e} ---\n"
    finally:
        if os.path.exists(temp_script_dir) and not TRACER_CONFIG["retain_temp_files"]:
            try:
                shutil.rmtree(temp_script_dir)
                logger.debug(
                    f"Cleaned up temporary subprocess runner script directory: {temp_script_dir}"
                )
            except Exception:
                pass

    return {
        "return_code": return_code,
        "stdout": stdout_data,
        "stderr": stderr_data,
        "duration_seconds": time.monotonic() - start_time,
    }


# --- PLUGIN FUNCTIONALITY ---
async def analyze_runtime_behavior(
    target_code_path: str,
    analysis_duration_seconds: int = TRACER_CONFIG["max_trace_duration_seconds"],
    test_script_path: Optional[str] = None,
    execution_args: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Analyzes target code's runtime behavior in an isolated subprocess,
    focusing on dynamic calls (exec/eval/__import__) and capturing error traces.
    """
    TRACE_ANALYSIS_ATTEMPTS.inc()
    start_time = time.monotonic()
    run_id = f"run-{uuid.uuid4().hex[:12]}"

    sandbox_mode = (
        "container"
        if TRACER_CONFIG["use_docker_sandbox"]
        else (
            "unsafe"
            if TRACER_CONFIG["allow_unsafe_non_containerized_run"]
            else "disabled"
        )
    )
    RUNTIME_TRACER_RUNS_TOTAL.labels(sandbox_mode=sandbox_mode).inc()

    result: Dict[str, Any] = {
        "success": False,
        "reason": "Analysis failed.",
        "dynamic_calls": [],
        "exceptions_captured": [],
        "raw_trace_log": [],
        "behavioral_healing_insights": [],
        "subprocess_log": {},
        "analysis_duration_seconds": 0.0,
        "run_id": run_id,
        "sandbox_mode": sandbox_mode,
    }

    if not os.path.exists(target_code_path):
        error_msg = f"Target code path not found: {target_code_path}"
        logger.error(error_msg)
        result["reason"] = error_msg
        TRACE_ANALYSIS_ERRORS.labels(error_type="target_code_not_found").inc()
        return result

    # Determine temp dir for trace log file
    temp_log_dir = TRACER_CONFIG["base_temp_dir"] or tempfile.gettempdir()
    os.makedirs(temp_log_dir, exist_ok=True)
    trace_log_file = os.path.join(temp_log_dir, f"sfe_trace_output_{run_id}.json")

    try:
        # Run target code
        subprocess_output = await _run_target_code_in_subprocess(
            target_code_path=target_code_path,
            trace_log_file=trace_log_file,
            analysis_duration_seconds=analysis_duration_seconds,
            test_script_path=test_script_path,
            execution_args=execution_args,
            run_id=run_id,
        )
        result["subprocess_log"] = subprocess_output
        result["analysis_duration_seconds"] = time.monotonic() - start_time

        if subprocess_output["return_code"] != 0:
            result["reason"] = (
                f"Target code execution failed in subprocess (exit code {subprocess_output['return_code']})."
            )
            result["error"] = subprocess_output["stderr"]
            logger.error(result["reason"])
            TRACE_ANALYSIS_ERRORS.labels(error_type="subprocess_failed").inc()
            return result

        # Load and parse trace log
        trace_data: List[Dict[str, Any]] = []
        if os.path.exists(trace_log_file):
            try:
                with open(trace_log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            entry = json.loads(line)
                            # Defensive truncation of fields that might explode
                            if isinstance(entry, dict):
                                if "code_line" in entry and isinstance(
                                    entry["code_line"], str
                                ):
                                    entry["code_line"] = _truncate(
                                        entry["code_line"],
                                        TRACER_CONFIG["redact_args_threshold"],
                                    )
                                if "message" in entry and isinstance(
                                    entry["message"], str
                                ):
                                    entry["message"] = _truncate(
                                        entry["message"],
                                        TRACER_CONFIG["max_trace_entry_len"],
                                    )
                            trace_data.append(entry)
                        except json.JSONDecodeError as e:
                            logger.warning(
                                f"Skipping malformed JSON line in trace log: {line.strip()} - {e}"
                            )
            except Exception as e:
                logger.error(f"Failed to read trace log file {trace_log_file}: {e}")
                result["reason"] = f"Failed to read trace log file: {e}"
                TRACE_ANALYSIS_ERRORS.labels(error_type="read_log_failed").inc()
                return result
        else:
            result["reason"] = (
                "Runtime analysis completed, but no trace log file was created (possible immediate crash or empty execution)."
            )
            TRACE_ANALYSIS_ERRORS.labels(error_type="no_trace_file").inc()
            logger.error(result["reason"])
            return result

        result["raw_trace_log"] = trace_data

        # Success criteria: subprocess ok and log readable; even if no events found, analysis succeeded
        result["success"] = True
        result["reason"] = "Runtime analysis completed."

        # Process insights and metrics
        for entry in trace_data:
            etype = entry.get("type")
            if etype == "dynamic_call_detected":
                result["dynamic_calls"].append(entry)
                DYNAMIC_CALLS_DETECTED.labels(
                    call_type=entry.get("call_type", "unknown")
                ).inc()
                result["behavioral_healing_insights"].append(
                    f"Detected dynamic call '{entry.get('code_line','')}' in {entry.get('file','?')}:{entry.get('line','?')}. "
                    "Consider refactoring to static/explicit calls for security and maintainability."
                )
            elif etype == "exception":
                result["exceptions_captured"].append(entry)
                RUNTIME_EXCEPTIONS_CAPTURED.labels(
                    exception_type=entry.get("exception_type", "unknown")
                ).inc()
                result["behavioral_healing_insights"].append(
                    f"Captured runtime exception '{entry.get('exception_type','?')}: {entry.get('message','')}' in {entry.get('file','?')}:{entry.get('line','?')}."
                )
            elif etype == "fatal_error":
                # This shouldn't happen if return_code was 0, but handle anyway
                result["reason"] = (
                    f"Fatal error within traced subprocess: {entry.get('message','')}"
                )
                result["error"] = entry.get("traceback", "No traceback available.")
                result["success"] = False
                TRACE_ANALYSIS_ERRORS.labels(error_type="subprocess_fatal").inc()
                logger.error(result["reason"])

        # Optional: if no entries at all, keep success but clarify
        if not trace_data:
            result["reason"] = "Analysis succeeded, but no trace events were captured."

        TRACE_ANALYSIS_SUCCESS.inc()
        TRACE_EXECUTION_LATENCY_SECONDS.observe(time.monotonic() - start_time)
        return result

    except Exception as e:
        result["error"] = str(e)
        result["reason"] = f"Runtime analysis failed due to unexpected exception: {e}"
        logger.error(
            f"Unexpected error in analyze_runtime_behavior: {e}", exc_info=True
        )
        TRACE_ANALYSIS_ERRORS.labels(error_type="plugin_unexpected").inc()
        return result
    finally:
        if os.path.exists(trace_log_file) and not TRACER_CONFIG["retain_temp_files"]:
            try:
                os.remove(trace_log_file)
                logger.debug(f"Cleaned up main trace log file: {trace_log_file}")
            except Exception:
                pass


# --- Auto-registration with core system ---
def register_plugin_entrypoints(register_func: Callable):
    """
    Registers this plugin's runtime behavior analysis function with the SFE core.
    """
    logger.info("Registering RuntimeTracerPlugin entrypoints...")
    register_func(
        name="runtime_tracer",
        executor_func=analyze_runtime_behavior,
        capabilities=["runtime_tracing", "dynamic_code_analysis"],
    )


# --- Standalone test harness ---
if __name__ == "__main__":
    _mock_registered_plugins: Dict[str, Any] = {}

    def _mock_register_analysis_pass(
        name: str, executor_func: Callable, capabilities: List[str]
    ):
        _mock_registered_plugins[name] = {
            "executor_func": executor_func,
            "capabilities": capabilities,
        }
        print(
            f"Mocked registration: Registered analysis pass '{name}' with capabilities: {capabilities}."
        )

    # Fix: pass the function, not the dict
    register_plugin_entrypoints(_mock_register_analysis_pass)

    async def main_test_run():
        print("\n--- Runtime Tracer Plugin Standalone Test ---")

        print("\n--- Running Plugin Health Check ---")
        health_status = await plugin_health()
        print(f"Health Status: {health_status['status']}")
        for detail in health_status["details"]:
            print(f"  - {detail}")

        if health_status["status"] == "error":
            print("\n--- Skipping Runtime Analysis Test: Plugin not healthy. ---")
            return

        # Basic target with dynamic call and exception
        import tempfile as _tf

        temp_code_dir = _tf.mkdtemp(prefix="sfe_trace_code_")
        target_file_path = os.path.join(temp_code_dir, "my_app.py")
        with open(target_file_path, "w", encoding="utf-8") as f:
            f.write("""\
import sys, os, time
def dynamic_loader(module_name):
    return __import__(module_name)
def dangerous_exec(code_str):
    exec(code_str)
def might_fail(x):
    if x > 5: raise ValueError("Value too high!")
    return x * 2
if __name__ == '__main__':
    dynamic_loader("math")
    dangerous_exec("a=3;print(a)")
    try: might_fail(7)
    except ValueError as e: print("Caught:", e)
""")
        analysis_result = await analyze_runtime_behavior(
            target_code_path=target_file_path, analysis_duration_seconds=10
        )
        print("\nAnalysis Result:")
        print(json.dumps(analysis_result, indent=2))
        shutil.rmtree(temp_code_dir, ignore_errors=True)

    asyncio.run(main_test_run())
