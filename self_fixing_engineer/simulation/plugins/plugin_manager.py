# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import ast
import asyncio
import functools
import glob
import importlib.util
import inspect
import json
import logging
import os
import shutil
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Module identity unification
# Prevent this file from being executed twice under different names (e.g.,
# 'plugin_manager' AND 'simulation.plugins.plugin_manager'), which would
# otherwise re-register Prometheus metrics and cause duplicate timeseries.
# ---------------------------------------------------------------------------
_this_module = sys.modules.get(__name__)
sys.modules.setdefault("simulation.plugins.plugin_manager", _this_module)
sys.modules.setdefault("plugin_manager", _this_module)

# --- Standard Python Libraries for Robustness ---
import importlib

# --- Optional Libraries for Enhancements ---
try:
    import portalocker  # noqa: F401

    _FILE_LOCKING_AVAILABLE = True
    logging.getLogger(__name__).debug("Using portalocker for file locking.")
except ImportError:
    try:
        import fcntl  # noqa: F401

        _FILE_LOCKING_AVAILABLE = True
        logging.getLogger(__name__).debug("Using fcntl for file locking on POSIX.")
    except ImportError:
        _FILE_LOCKING_AVAILABLE = False
        logging.getLogger(__name__).warning(
            "No suitable file locking library found (portalocker or fcntl). Plugin manager file operations may not be atomic under heavy concurrency. Install 'portalocker' for robust locking."
        )

try:
    from pydantic import BaseModel, Field, ValidationError

    pydantic_available = True
except ImportError:
    pydantic_available = False

# Tenacity with safe fallbacks and a wrapper to avoid passing fallback args
try:
    from tenacity import retry as _tenacity_retry
    from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

    tenacity_available = True

    def with_retry(stop, wait, retry_exc):
        return _tenacity_retry(stop=stop, wait=wait, retry=retry_exc)

except ImportError:
    tenacity_available = False

    def with_retry(*_a, **_k):  # type: ignore
        def _wrap(fn):
            return fn

        return _wrap

    def stop_after_attempt(*_a, **_k):  # type: ignore
        return None

    def wait_exponential(*_a, **_k):  # type: ignore
        return None

    def retry_if_exception_type(*_a, **_k):  # type: ignore
        return None


try:
    from prometheus_client import REGISTRY, Counter, Gauge

    prometheus_available = True
except ImportError:
    prometheus_available = False

try:
    from detect_secrets.core import SecretsCollection

    detect_secrets_available = True
except ImportError:
    detect_secrets_available = False

try:
    from RestrictedPython import compile_restricted, safe_builtins

    restricted_python_available = True
except ImportError:
    restricted_python_available = False

# Robust version parsing
try:
    from packaging.version import Version as _Version

    def _parse_version(s: str) -> _Version:
        return _Version(s)

except ImportError:

    def _parse_version(s: str):
        # Fallback: parse numeric components only
        parts = []
        for p in s.split("."):
            try:
                parts.append(int("".join(ch for ch in p if ch.isdigit()) or "0"))
            except ValueError:
                parts.append(0)
        return tuple(parts)


# Configuration via environment variables
# Isolation defaults to "process" for safety. Set PLUGIN_MANAGER_PYTHON_ISOLATION=inproc for trusted environments/tests.
PYTHON_ISOLATION_MODE = os.getenv("PLUGIN_MANAGER_PYTHON_ISOLATION", "").lower() or (
    "process" if (os.getenv("CI") or os.getenv("GITHUB_ACTIONS")) else "process"
)
# Allowed: "process" (default), "inproc"
HEALTH_TIMEOUT_SEC = float(os.getenv("PLUGIN_MANAGER_HEALTH_TIMEOUT_SEC", "10.0"))

# Plugin logger - assume configured externally
plugin_logger = logging.getLogger("simulation.plugins")

# Dynamic handler registration
HANDLERS: Dict[str, Any] = {}

# Pydantic model for manifest validation
if pydantic_available:

    class PluginManifest(BaseModel):
        name: str
        version: str
        type: str
        entrypoint: str
        health_check: str
        api_version: str
        manifest_version: str
        author: str
        capabilities: List[str] = Field(default_factory=list)
        permissions: List[str] = Field(default_factory=list)
        dependencies: List[str] = Field(default_factory=list)
        min_core_version: str
        max_core_version: str
        license: Optional[str] = None
        homepage: Optional[str] = None
        tags: List[str] = Field(default_factory=list)
        sandbox: Dict[str, Any] = Field(default_factory=dict)


# Prometheus Metrics
#
# We must make metric creation idempotent at the process level in case some
# loader still manages to import this module twice. We anchor a small cache in
# a process-global singleton module so *all* imports share it.
#
if prometheus_available:
    _metrics_singleton = sys.modules.setdefault(
        "simulation._metrics_singleton", type(sys)("simulation._metrics_singleton")
    )
    if not hasattr(_metrics_singleton, "CACHE"):
        _metrics_singleton.CACHE = {}

    def _metric_key(kind: str, name: str) -> tuple[str, str]:
        return (kind, name)

    def _get_or_create_counter(name: str, doc: str, labelnames=(), registry=REGISTRY):
        k = _metric_key("counter", name)
        m = _metrics_singleton.CACHE.get(k)
        if m is None:
            try:
                m = Counter(name, doc, tuple(labelnames), registry=registry)
            except ValueError:
                # If already registered by a prior import path, reuse cached object
                # from our singleton if present (another thread may have just set it)
                m = _metrics_singleton.CACHE.get(k)
                if m is None:
                    # As a last resort, create a logically distinct metric name to avoid hard crash.
                    # (Should never happen once module identity is unified above.)
                    m = Counter(
                        f"{name}_dupe_suppressed",
                        doc,
                        tuple(labelnames),
                        registry=registry,
                    )
            _metrics_singleton.CACHE[k] = m
        return m

    def _get_or_create_gauge(name: str, doc: str, labelnames=(), registry=REGISTRY):
        k = _metric_key("gauge", name)
        m = _metrics_singleton.CACHE.get(k)
        if m is None:
            try:
                m = Gauge(name, doc, tuple(labelnames), registry=registry)
            except ValueError:
                m = _metrics_singleton.CACHE.get(k)
                if m is None:
                    m = Gauge(
                        f"{name}_dupe_suppressed",
                        doc,
                        tuple(labelnames),
                        registry=registry,
                    )
            _metrics_singleton.CACHE[k] = m
        return m

    # Define module metrics idempotently
    PLUGIN_LOADS_TOTAL = _get_or_create_counter(
        "plugin_loads_total", "Total plugin load attempts", ["plugin_type", "status"]
    )
    PLUGIN_ERRORS_TOTAL = _get_or_create_counter(
        "plugin_errors_total",
        "Total errors during plugin operations",
        ["error_type", "plugin_name"],
    )
    PLUGIN_HEALTH_STATUS = _get_or_create_gauge(
        "plugin_health_status", "Binary plugin health (1=ok,0=bad)", ["plugin_name"]
    )


def retry_decorator(exc_type):
    """Clarity wrapper for tenacity decorator selection."""
    if tenacity_available:
        return with_retry(
            stop_after_attempt(3),
            wait_exponential(min=2, max=10),
            retry_if_exception_type(exc_type),
        )
    return lambda f: f


# --- Dynamic Handler Wrappers ---
try:
    from plugins.wasm_runner import WasmRunner, WasmRunnerError

    class WasmPluginWrapper:
        """
        Wrapper for WASM plugins. Handles initialization and execution via a WasmRunner instance.
        """

        def __init__(self, name: str, manifest: Dict[str, Any], plugins_dir: Path):
            self.name = name
            self.manifest = manifest
            self.plugins_dir = plugins_dir
            self.runner: Optional[WasmRunner] = None
            self._initialize_runner()
            plugin_logger.info(f"[{self.name}] Initialized WASM plugin wrapper.")

        @retry_decorator(WasmRunnerError)
        def _initialize_runner(self):
            """Initializes the WasmRunner with retries on failure."""
            try:
                self.runner = WasmRunner(self.name, self.manifest, self.plugins_dir)
            except (WasmRunnerError, FileNotFoundError) as e:
                plugin_logger.error(
                    f"[{self.name}] Failed to initialize WasmRunner: {e}"
                )
                self.runner = None
                raise

        async def health(self) -> Dict[str, Any]:
            if not self.runner:
                return {"status": "error", "message": "WASM runner not initialized."}
            try:
                return await asyncio.wait_for(
                    self.runner.plugin_health(), timeout=HEALTH_TIMEOUT_SEC
                )
            except asyncio.TimeoutError:
                return {
                    "status": "error",
                    "message": f"WASM health timeout after {HEALTH_TIMEOUT_SEC}s",
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"WASM plugin health check failed: {e}",
                }

        async def run(self, func_name: str, *args: Any) -> Any:
            if not self.runner:
                raise RuntimeError("WASM runner not initialized.")
            return await self.runner.run_function(func_name, *args)

        async def close(self) -> None:
            if self.runner:
                try:
                    await self.runner.close()
                except Exception:
                    pass

    HANDLERS["wasm"] = WasmPluginWrapper
except ImportError as e:
    plugin_logger.warning(
        f"wasm_runner.py not found or failed to import: {e}. WASM plugin support will be limited."
    )

try:
    from plugins.grpc_runner import GrpcRunner, GrpcRuntimeError

    class GrpcPluginWrapper:
        """
        Wrapper for gRPC plugins. Manages the connection and method calls to an external gRPC service.
        """

        def __init__(self, name: str, manifest: Dict[str, Any]):
            self.name = name
            self.manifest = manifest
            self.runner: Optional[GrpcRunner] = None
            self._initialize_runner()
            plugin_logger.info(f"[{self.name}] Initialized gRPC plugin wrapper.")

        @retry_decorator(GrpcRuntimeError)
        def _initialize_runner(self):
            """Initializes the GrpcRunner with retries."""
            try:
                self.runner = GrpcRunner(self.name, self.manifest)
            except GrpcRuntimeError as e:
                plugin_logger.error(
                    f"[{self.name}] Failed to initialize GrpcRunner: {e}"
                )
                self.runner = None
                raise

        async def health(self) -> Dict[str, Any]:
            if not self.runner:
                return {"status": "error", "message": "gRPC runner not initialized."}
            try:
                await self.runner.connect()
                return await asyncio.wait_for(
                    self.runner.plugin_health(), timeout=HEALTH_TIMEOUT_SEC
                )
            except asyncio.TimeoutError:
                return {
                    "status": "error",
                    "message": f"gRPC health timeout after {HEALTH_TIMEOUT_SEC}s",
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"gRPC plugin health check failed: {e}",
                }

        async def run(
            self, service_method: str, request_data: Dict[str, Any]
        ) -> Dict[str, Any]:
            if not self.runner:
                raise RuntimeError("gRPC runner not initialized.")
            await self.runner.connect()
            return await self.runner.run_method(service_method, request_data)

        async def close(self) -> None:
            if self.runner:
                try:
                    await self.runner.close()
                except Exception:
                    pass

    HANDLERS["grpc"] = GrpcPluginWrapper
except ImportError as e:
    plugin_logger.warning(
        f"grpc_runner.py not found or failed to import: {e}. gRPC plugin support will be limited."
    )

# Define __all__ for explicit exports
__all__ = ["PluginManager", "HANDLERS"]

# Define CORE_SIM_RUNNER_VERSION (assuming it comes from utils or a central config)
try:
    from utils import CORE_SIM_RUNNER_VERSION
except ImportError:
    CORE_SIM_RUNNER_VERSION = "0.0.0"

# --- Constants from config (could be loaded from JSON) ---
MIN_MANIFEST_VERSION = "2.0"
DANGEROUS_PYTHON_MODULES = set(sys.builtin_module_names).union(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "importlib",
        "socket",
        "threading",
        "multiprocessing",
        "ctypes",
        "site",
        "inspect",
    }
)


def _extract_manifest_from_python_file(py_file: Path) -> Optional[Dict[str, Any]]:
    """
    Safely extract a top-level PLUGIN_MANIFEST from a Python file without executing the code.
    Only literal dict/list/str/num/bool/None are allowed via ast.literal_eval.
    """
    try:
        src = py_file.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(py_file))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "PLUGIN_MANIFEST":
                        return ast.literal_eval(node.value)  # safe for literals
            elif isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "PLUGIN_MANIFEST"
                    and node.value is not None
                ):
                    return ast.literal_eval(node.value)
        plugin_logger.warning(f"{py_file}: No top-level PLUGIN_MANIFEST literal found.")
        return None
    except Exception as e:
        plugin_logger.error(f"Failed to extract PLUGIN_MANIFEST from {py_file}: {e}")
        return None


def _minimal_manifest_validate(manifest: Dict[str, Any]) -> Tuple[bool, str]:
    """Fallback manifest validation when Pydantic is unavailable."""
    required = [
        "name",
        "version",
        "type",
        "entrypoint",
        "api_version",
        "manifest_version",
        "min_core_version",
        "max_core_version",
    ]
    for k in required:
        if k not in manifest:
            return False, f"Missing required manifest field: {k}"
    if not isinstance(manifest.get("name"), str) or not manifest["name"]:
        return False, "Invalid name"
    if not isinstance(manifest.get("type"), str) or manifest["type"] not in (
        "python",
        "wasm",
        "grpc",
    ):
        return False, "Invalid type"
    if not isinstance(manifest.get("entrypoint"), str) or not manifest["entrypoint"]:
        return False, "Invalid entrypoint"
    if manifest.get("type") == "python" and "health_check" not in manifest:
        return False, "Missing health_check for python plugin"
    # caps/tags typing
    for list_key in ("capabilities", "tags", "permissions", "dependencies"):
        val = manifest.get(list_key, [])
        if not isinstance(val, list) or any(not isinstance(x, str) for x in val):
            return False, f"Field '{list_key}' must be a list of strings"
    # Version window check
    try:
        cur = _parse_version(CORE_SIM_RUNNER_VERSION)
        vmin = _parse_version(manifest["min_core_version"])
        if cur < vmin:
            return False, f"Requires core >= {manifest['min_core_version']}"
    except Exception:
        pass
    # manifest_version
    try:
        if _parse_version(manifest["manifest_version"]) < _parse_version(
            MIN_MANIFEST_VERSION
        ):
            return False, f"Manifest version too old (< {MIN_MANIFEST_VERSION})"
    except Exception:
        pass
    return True, ""


class PythonSubprocessProxy:
    """
    Process-isolated proxy for Python plugins.
    Executes operations (currently: health) in a short-lived subprocess to avoid in-proc code execution.
    """

    def __init__(
        self, plugin_path: Path, manifest: Dict[str, Any], health_timeout: float
    ):
        self.plugin_path = plugin_path
        self.manifest = manifest
        self.health_timeout = health_timeout

    async def health(self) -> Dict[str, Any]:
        code = r"""
import sys, os, json, importlib.util, asyncio
from pathlib import Path
def load_module_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)  # type: ignore
    return mod

async def main():
    manifest = json.loads(os.environ["PM_MANIFEST"])
    plugin_path = Path(os.environ["PM_PYPATH"])
    entry = manifest.get("entrypoint","")
    health_name = manifest.get("health_check")
    try:
        if plugin_path.is_file():
            module = load_module_from_path(plugin_path.stem, str(plugin_path))
        else:
            if entry.endswith(".py"):
                target = plugin_path / entry
                if not target.exists():
                    print(json.dumps({"status":"error","message": f"Entrypoint not found: {target}"}))
                    return
                module = load_module_from_path(plugin_path.name, str(target))
            else:
                print(json.dumps({"status":"error","message":"Module-style entrypoints not supported in process isolation"}))
                return
        if not hasattr(module, health_name):
            print(json.dumps({"status":"error","message": f"Health method '{health_name}' not found"}))
            return
        fn = getattr(module, health_name)
        if asyncio.iscoroutinefunction(fn):
            res = await fn()
        else:
            res = fn()
        if isinstance(res, dict) and "status" in res:
            print(json.dumps(res))
        else:
            print(json.dumps({"status":"ok","message":str(res)}))
    except Exception as e:
        print(json.dumps({"status":"error","message":str(e)}))

asyncio.run(main())
"""
        env = os.environ.copy()
        env["PM_MANIFEST"] = json.dumps(self.manifest, ensure_ascii=False)
        env["PM_PYPATH"] = str(self.plugin_path)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=self.health_timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "status": "error",
                "message": f"Python plugin health timeout after {self.health_timeout}s",
            }
        if proc.returncode != 0 and not out:
            return {
                "status": "error",
                "message": f"Python plugin process failed: {err.decode(errors='replace')[:1024]}",
            }
        try:
            return json.loads(out.decode(errors="replace").strip() or "{}") or {
                "status": "error",
                "message": "Empty health response",
            }
        except Exception as e:
            return {"status": "error", "message": f"Invalid health response: {e}"}

    async def close(self):
        # No persistent process; nothing to close
        return


class PluginManager:
    """
    Universal, Secure, Polyglot Plugin Manager.

    Internals:
    - A dedicated background event loop runs in a daemon thread for safe sync->async bridging.
      Use _run_coro_blocking() to schedule work there. Call stop_background_loop() on shutdown.
    """

    def __init__(self, plugins_dir: Optional[str] = None):
        if plugins_dir is None:
            base_dir = Path(os.path.dirname(os.path.abspath(__file__)))
            self.plugins_dir = base_dir / "plugins"
        else:
            self.plugins_dir = Path(plugins_dir)
        self.registry: Dict[str, Dict[str, Any]] = {}
        self._registry_lock = threading.Lock()
        self._bg_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bg_thread: Optional[threading.Thread] = None
        self._ensure_background_loop()

    def _ensure_background_loop(self):
        if self._bg_loop and self._bg_loop.is_running():
            return

        def _loop_runner(loop: asyncio.AbstractEventLoop):
            asyncio.set_event_loop(loop)
            loop.run_forever()

        self._bg_loop = asyncio.new_event_loop()
        self._bg_thread = threading.Thread(
            target=_loop_runner, args=(self._bg_loop,), daemon=True
        )
        self._bg_thread.start()

    def stop_background_loop(self):
        """Stop and join the dedicated background event loop thread."""
        if not self._bg_loop:
            return
        try:
            self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
        except Exception:
            pass
        if self._bg_thread and self._bg_thread.is_alive():
            self._bg_thread.join(timeout=5)
        self._bg_loop = None
        self._bg_thread = None

    async def discover_plugins(self) -> List[Path]:
        """Find all plugin files and WASM/manifest folders in plugins_dir asynchronously."""
        loop = asyncio.get_running_loop()
        py_plugins_coro = loop.run_in_executor(
            None, glob.glob, str(self.plugins_dir / "*.py")
        )
        manifest_dirs_coro = loop.run_in_executor(
            None, glob.glob, str(self.plugins_dir / "*")
        )

        py_plugins, manifest_dirs = await asyncio.gather(
            py_plugins_coro, manifest_dirs_coro
        )

        all_plugins: List[Path] = []
        for p in py_plugins:
            pth = Path(p)
            # Ignore generated caches
            if pth.name.startswith(".") or pth.name == "__init__.py":
                continue
            all_plugins.append(pth)

        for d in manifest_dirs:
            d_path = Path(d)
            if not d_path.is_dir():
                continue
            if d_path.name.startswith(".") or d_path.name == "__pycache__":
                continue
            if (d_path / "manifest.json").exists():
                all_plugins.append(d_path)

        return all_plugins

    @functools.lru_cache(maxsize=100)
    def load_manifest(self, plugin_path: Path) -> Optional[Dict[str, Any]]:
        """Load manifest from a Python file or a manifest.json in a folder, with caching, without executing plugin code."""
        if plugin_path.is_dir():
            manifest_file = plugin_path / "manifest.json"
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                return manifest
            except (FileNotFoundError, json.JSONDecodeError, PermissionError) as e:
                plugin_logger.error(f"Failed to load manifest for {plugin_path}: {e}")
                return None
        else:
            # Python file: extract PLUGIN_MANIFEST literal safely
            return _extract_manifest_from_python_file(plugin_path)

    def _health_timeout_for(self, manifest: Dict[str, Any]) -> float:
        """Determine health timeout per-plugin (optional override via manifest.sandbox.resource_limits.health_timeout_seconds)."""
        try:
            limits = (manifest or {}).get("sandbox", {}).get("resource_limits", {})
            for key in ("health_timeout_seconds", "health_timeout_sec"):
                if key in limits:
                    return float(limits[key])
        except Exception:
            pass
        return HEALTH_TIMEOUT_SEC

    def _validate_manifest_schema(
        self, plugin_path: Path, manifest: Dict[str, Any]
    ) -> bool:
        """Performs manifest validation including Pydantic and security checks."""
        # Structural validation
        if pydantic_available:
            try:
                PluginManifest.parse_obj(manifest)
            except ValidationError as e:
                plugin_logger.error(f"Manifest failed Pydantic validation: {e}")
                return False
        else:
            ok, msg = _minimal_manifest_validate(manifest)
            if not ok:
                plugin_logger.error(f"Manifest validation failed: {msg}")
                return False

        # Enforce manifest_version policy
        try:
            if _parse_version(manifest.get("manifest_version", "0.0")) < _parse_version(
                MIN_MANIFEST_VERSION
            ):
                plugin_logger.error(
                    f"Manifest version {manifest.get('manifest_version')} < {MIN_MANIFEST_VERSION}. Rejected."
                )
                return False
        except Exception:
            pass

        plugin_type = manifest.get("type")

        if any(
            perm in manifest.get("permissions", [])
            for perm in ["execute_arbitrary_code", "network_unrestricted"]
        ):
            plugin_logger.error(
                f"[{manifest.get('name','?')}] Dangerous permissions detected."
            )
            return False

        if plugin_type == "python":
            if manifest.get("sandbox", {}).get("enabled"):
                plugin_logger.warning(
                    f"Python plugin '{manifest['name']}' has sandbox enabled. Python sandboxing is limited; consider WASM/gRPC for better isolation."
                )
            # Prevent shadowing key stdlib/builtin names
            if (
                manifest["name"] in DANGEROUS_PYTHON_MODULES
                or manifest["name"] in sys.builtin_module_names
            ):
                plugin_logger.error(
                    f"Python plugin '{manifest['name']}' uses a disallowed module name. Rejected."
                )
                return False
            # For directory-based python plugins, enforce file-path entrypoint to avoid sys.path injection
            if plugin_path.is_dir() and not manifest.get("entrypoint", "").endswith(
                ".py"
            ):
                plugin_logger.error(
                    f"Python plugin '{manifest['name']}' directory entrypoint must be a .py file path (not module path). Rejected."
                )
                return False

        elif plugin_type == "wasm":
            sandbox = manifest.get("sandbox", {})
            if not sandbox.get("enabled"):
                plugin_logger.error(
                    f"WASM plugin '{manifest['name']}' must enable sandboxing. Rejected."
                )
                return False
            if "resource_limits" not in sandbox:
                plugin_logger.error(
                    f"WASM plugin '{manifest['name']}' must specify 'resource_limits'. Rejected."
                )
                return False
            # Strict .wasm presence: check entrypoint if it points to a file, else any .wasm in dir
            if plugin_path.is_dir():
                ep = manifest.get("entrypoint", "")
                if ep.endswith(".wasm"):
                    target = plugin_path / ep
                    if not target.exists():
                        plugin_logger.error(
                            f"WASM entrypoint file not found: {target}. Rejected."
                        )
                        return False
                else:
                    if not any(plugin_path.glob("*.wasm")):
                        plugin_logger.error(
                            f"No .wasm binary found for WASM plugin '{manifest['name']}'. Rejected."
                        )
                        return False

        elif plugin_type == "grpc":
            sandbox = manifest.get("sandbox", {})
            if not sandbox.get("enabled"):
                plugin_logger.error(
                    f"gRPC plugin '{manifest['name']}' must enable sandboxing. Rejected."
                )
                return False
            ep = manifest.get("entrypoint", "")
            if not ep.startswith("grpc://"):
                plugin_logger.error(
                    f"gRPC plugin '{manifest['name']}' requires 'entrypoint' to be a gRPC URL. Rejected."
                )
                return False

        # Version window checks
        current_core_version = CORE_SIM_RUNNER_VERSION
        min_version = manifest.get("min_core_version")
        max_version = manifest.get("max_core_version")
        try:
            if min_version and _parse_version(current_core_version) < _parse_version(
                min_version
            ):
                plugin_logger.error(
                    f"Plugin requires core version >= {min_version}, current is {current_core_version}. Rejected."
                )
                return False
            if max_version and _parse_version(current_core_version) > _parse_version(
                max_version
            ):
                plugin_logger.warning(
                    f"Plugin supports up to core version {max_version}, current is {current_core_version}. Compatibility issues may arise."
                )
        except Exception:
            pass

        return True

    def _import_python_module_inproc(
        self, plugin_name: str, plugin_path: Path, manifest: Dict[str, Any]
    ) -> Tuple[Any, Dict[str, Any]]:
        """
        Import a Python plugin module in-process after manifest validation.
        Supports:
          - Single-file plugin (.py)
          - Directory plugin with entrypoint as a .py file path relative to dir (module paths are rejected)
        Respects sandbox.enabled for single-file entrypoints (best effort).
        """
        sandbox_enabled = bool(manifest.get("sandbox", {}).get("enabled"))
        entrypoint = manifest.get("entrypoint", "")
        load_info: Dict[str, Any] = {"kind": None}

        if plugin_path.is_file():
            # Single-file plugin
            if sandbox_enabled and restricted_python_available:
                with open(plugin_path, "r", encoding="utf-8") as f:
                    safe_globals = {"__builtins__": safe_builtins}
                    restricted_code = compile_restricted(f.read(), "<string>", "exec")
                    module = importlib.util.module_from_spec(
                        importlib.util.spec_from_loader(plugin_name, None)
                    )
                    exec(restricted_code, safe_globals)
                    for key, value in safe_globals.items():
                        if not key.startswith("__"):
                            setattr(module, key, value)
                load_info = {"kind": "file", "path": str(plugin_path)}
                return module, load_info
            else:
                spec = importlib.util.spec_from_file_location(plugin_name, plugin_path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[plugin_name] = module
                assert spec and spec.loader
                spec.loader.exec_module(module)  # type: ignore
                load_info = {"kind": "file", "path": str(plugin_path)}
                return module, load_info

        # Directory plugin with file entrypoint (enforced in validation)
        base_dir = plugin_path
        target = base_dir / entrypoint
        if not target.exists():
            raise FileNotFoundError(f"Entrypoint file not found: {target}")
        if sandbox_enabled and restricted_python_available:
            with open(target, "r", encoding="utf-8") as f:
                safe_globals = {"__builtins__": safe_builtins}
                restricted_code = compile_restricted(f.read(), "<string>", "exec")
                module = importlib.util.module_from_spec(
                    importlib.util.spec_from_loader(plugin_name, None)
                )
                exec(restricted_code, safe_globals)
                for key, value in safe_globals.items():
                    if not key.startswith("__"):
                        setattr(module, key, value)
            load_info = {"kind": "file", "path": str(target)}
            return module, load_info
        else:
            spec = importlib.util.spec_from_file_location(plugin_name, target)
            module = importlib.util.module_from_spec(spec)
            sys.modules[plugin_name] = module
            assert spec and spec.loader
            spec.loader.exec_module(module)  # type: ignore
            load_info = {"kind": "file", "path": str(target)}
            return module, load_info

    def _get_python_instance(
        self, plugin_name: str, plugin_path: Path, manifest: Dict[str, Any]
    ) -> Tuple[Any, Dict[str, Any]]:
        """
        Depending on isolation mode, return an instance:
          - process: PythonSubprocessProxy
          - inproc: in-process module
        """
        mode = PYTHON_ISOLATION_MODE
        if mode not in ("process", "inproc"):
            mode = "process"
        if mode == "process":
            proxy = PythonSubprocessProxy(
                plugin_path, manifest, health_timeout=self._health_timeout_for(manifest)
            )
            load_info = {"kind": "process", "path": str(plugin_path)}
            return proxy, load_info
        else:
            return self._import_python_module_inproc(plugin_name, plugin_path, manifest)

    def load_plugin(self, plugin_path: Path, check_health: bool = False):
        """Load a plugin by path, routing to the appropriate handler based on manifest 'type'."""
        path_name = (
            plugin_path.stem if plugin_path.suffix == ".py" else plugin_path.name
        )
        plugin_type = "unknown"
        plugin_name = path_name  # default until manifest loaded

        try:
            manifest = self.load_manifest(plugin_path)
            if not manifest:
                raise ValueError("Missing or invalid manifest.")

            # Use manifest name as identity to decouple from filenames
            plugin_name = manifest.get("name", path_name)

            # Detect duplicates by manifest name
            with self._registry_lock:
                if plugin_name in self.registry and self.registry[plugin_name].get(
                    "status"
                ) in (
                    "loaded",
                    "warning",
                    "disabled",
                ):
                    raise ValueError(
                        f"Duplicate plugin name '{plugin_name}' detected. Unload or rename the existing plugin."
                    )

            plugin_type = manifest.get("type", "unknown")
            if not self._validate_manifest_schema(plugin_path, manifest):
                raise ValueError("Manifest schema validation failed.")

            if plugin_type == "python":
                instance, load_info = self._get_python_instance(
                    plugin_name, plugin_path, manifest
                )
            elif plugin_type in HANDLERS:
                handler_class = HANDLERS[plugin_type]
                if plugin_type == "wasm":
                    instance = handler_class(plugin_name, manifest, self.plugins_dir)
                else:
                    instance = handler_class(plugin_name, manifest)
                load_info = {"kind": plugin_type}
            else:
                raise ValueError(f"Unsupported plugin type: {plugin_type}")

            with self._registry_lock:
                self.registry[plugin_name] = {
                    "instance": instance,
                    "manifest": manifest,
                    "status": "loaded",
                    "error": None,
                    "path": str(plugin_path),
                    "load_info": load_info,
                }

            plugin_logger.info(
                f"[{plugin_name}] Loaded {plugin_type} plugin (isolation={ 'process' if plugin_type=='python' and isinstance(instance, PythonSubprocessProxy) else 'inproc' })."
            )
            if prometheus_available:
                PLUGIN_LOADS_TOTAL.labels(
                    plugin_type=plugin_type, status="success"
                ).inc()

            if check_health:
                initial_health = self._run_coro_blocking(self.health(plugin_name))
                status = (initial_health or {}).get("status")
                with self._registry_lock:
                    if status in ["error", "warning", "fail", "not_serving"]:
                        plugin_logger.warning(
                            f"[{plugin_name}] Initial health check reported: {initial_health.get('message', status)}. Status set to 'warning'."
                        )
                        self.registry[plugin_name]["status"] = "warning"
                    else:
                        plugin_logger.info(
                            f"[{plugin_name}] Initial health check: {status}."
                        )

        except Exception as e:
            with self._registry_lock:
                self.registry[plugin_name] = {
                    "status": "error",
                    "error": str(e),
                    "path": str(plugin_path),
                }
            plugin_logger.error(
                f"[{plugin_name}] Failed to load: {e}\n{traceback.format_exc()}"
            )
            self._audit_log_event(plugin_name, "load_failed", str(e))
            if prometheus_available:
                PLUGIN_LOADS_TOTAL.labels(plugin_type=plugin_type, status="error").inc()
                PLUGIN_ERRORS_TOTAL.labels(
                    error_type="load_failure", plugin_name=plugin_name
                ).inc()

    def reload_plugin(self, name: str):
        """Reloads a plugin by re-initializing its runner or re-importing the module."""
        # Snapshot necessary info
        with self._registry_lock:
            if name not in self.registry:
                plugin_logger.warning(f"[{name}] Cannot reload: plugin not loaded.")
                return
            entry = self.registry[name]
            manifest = entry.get("manifest", {})
            plugin_type = manifest.get("type") if manifest else "unknown"
            load_info = entry.get("load_info", {})
            plugin_path = Path(entry.get("path", ""))

            # Set status to reloading
            entry["status"] = "reloading"
            instance = entry.get("instance")

        plugin_logger.info(f"[{name}] Attempting to reload {plugin_type} plugin.")

        # Close outside lock: support async or sync close; use background loop + asyncio.to_thread for sync close
        try:
            if hasattr(instance, "close"):
                if asyncio.iscoroutinefunction(getattr(instance, "close")):
                    self._run_coro_blocking(instance.close())  # type: ignore
                else:
                    if self._bg_loop and self._bg_loop.is_running():
                        fut = asyncio.run_coroutine_threadsafe(asyncio.to_thread(instance.close), self._bg_loop)  # type: ignore
                        fut.result(timeout=HEALTH_TIMEOUT_SEC)
        except Exception:
            pass

        try:
            if plugin_type == "python":
                # Recreate according to isolation mode
                new_instance, load_info = self._get_python_instance(
                    name, plugin_path, manifest
                )
            elif plugin_type in HANDLERS:
                handler_class = HANDLERS[plugin_type]
                if plugin_type == "wasm":
                    new_instance = handler_class(name, manifest, self.plugins_dir)
                else:
                    new_instance = handler_class(name, manifest)
            else:
                raise ValueError(f"Reload not supported for plugin type: {plugin_type}")

            # Update registry
            with self._registry_lock:
                self.registry[name]["instance"] = new_instance
                self.registry[name]["status"] = "loaded"
                self.registry[name]["error"] = None
                self.registry[name]["load_info"] = load_info

            self._audit_log_event(name, "reloaded", "Plugin reloaded successfully")

            reloaded_health = self._run_coro_blocking(self.health(name))
            with self._registry_lock:
                if reloaded_health.get("status") in [
                    "error",
                    "warning",
                    "fail",
                    "not_serving",
                ]:
                    plugin_logger.warning(
                        f"[{name}] Post-reload health check reported: {reloaded_health.get('message', reloaded_health.get('status'))}. Status set to 'warning'."
                    )
                    self.registry[name]["status"] = "warning"
                else:
                    plugin_logger.info(
                        f"[{name}] Post-reload health check: {reloaded_health.get('status')}."
                    )

        except Exception as e:
            with self._registry_lock:
                entry = self.registry.get(name, {})
                entry["status"] = "error"
                entry["error"] = str(e)
            plugin_logger.error(
                f"[{name}] Reload failed: {e}\n{traceback.format_exc()}"
            )
            self._audit_log_event(name, "reload_failed", str(e))
            if prometheus_available:
                PLUGIN_ERRORS_TOTAL.labels(
                    error_type="reload_failure", plugin_name=name
                ).inc()

    def enable_plugin(self, name: str):
        """Enable a plugin (placeholder: in a real system, this would call hooks)."""
        with self._registry_lock:
            if name not in self.registry:
                plugin_logger.warning(f"[{name}] Cannot enable: plugin not registered.")
                return
            if self.registry[name]["status"] != "disabled":
                plugin_logger.info(
                    f"[{name}] Plugin not disabled; current status: {self.registry[name]['status']}"
                )
                return
            plugin_entry = self.registry[name]
            manifest = plugin_entry.get("manifest", {})
            plugin_type = manifest.get("type")

        plugin_logger.info(f"[{name}] Attempting to enable {plugin_type} plugin.")
        try:
            if plugin_type in HANDLERS:
                handler_class = HANDLERS[plugin_type]
                if plugin_type == "wasm":
                    new_instance = handler_class(name, manifest, self.plugins_dir)
                else:
                    new_instance = handler_class(name, manifest)
                with self._registry_lock:
                    self.registry[name]["instance"] = new_instance
            elif plugin_type == "python":
                # Recreate instance respecting isolation mode
                plugin_path = Path(self.registry[name]["path"])
                new_instance, load_info = self._get_python_instance(
                    name, plugin_path, manifest
                )
                with self._registry_lock:
                    self.registry[name]["instance"] = new_instance
                    self.registry[name]["load_info"] = load_info

            health_status = self._run_coro_blocking(self.health(name))
            with self._registry_lock:
                if health_status.get("status") in ["ok", "serving"]:
                    self.registry[name]["status"] = "loaded"
                    self.registry[name]["error"] = None
                    plugin_logger.info(f"[{name}] Enabled and healthy.")
                else:
                    self.registry[name]["status"] = "warning"
                    self.registry[name]["error"] = health_status.get(
                        "message", "Re-enable health check failed."
                    )
                    plugin_logger.warning(
                        f"[{name}] Enabled but health check failed: {self.registry[name]['error']}"
                    )
        except Exception as e:
            with self._registry_lock:
                self.registry[name]["status"] = "error"
                self.registry[name]["error"] = str(e)
            plugin_logger.error(
                f"[{name}] Failed to enable: {e}\n{traceback.format_exc()}"
            )
        self._audit_log_event(name, "enabled", "Plugin enabled")

    def disable_plugin(self, name: str):
        """Disable a plugin, calling `close` on runners and updating status."""
        with self._registry_lock:
            if name not in self.registry or self.registry[name]["status"] not in (
                "loaded",
                "warning",
            ):
                plugin_logger.warning(
                    f"[{name}] Cannot disable: status={self.registry.get(name,{}).get('status')}"
                )
                return
            plugin_entry = self.registry[name]
            instance = plugin_entry.get("instance")
            plugin_type = plugin_entry.get("manifest", {}).get("type")
            # Optimistically set status to disabling
            plugin_entry["status"] = "disabling"

        plugin_logger.info(f"[{name}] Attempting to disable {plugin_type} plugin.")
        try:
            if hasattr(instance, "close"):
                # Support async and sync close; use background loop + asyncio.to_thread for sync close
                if asyncio.iscoroutinefunction(getattr(instance, "close")):
                    self._run_coro_blocking(instance.close())  # type: ignore
                else:
                    if self._bg_loop and self._bg_loop.is_running():
                        fut = asyncio.run_coroutine_threadsafe(asyncio.to_thread(instance.close), self._bg_loop)  # type: ignore
                        fut.result(timeout=HEALTH_TIMEOUT_SEC)
                plugin_logger.info(f"[{name}] Plugin resources closed.")
            with self._registry_lock:
                self.registry[name]["status"] = "disabled"
                self.registry[name]["error"] = None
                plugin_logger.info(f"[{name}] Disabled.")
        except Exception as e:
            with self._registry_lock:
                self.registry[name]["status"] = "warning"
                self.registry[name]["error"] = f"Error during disable close: {e}"
            plugin_logger.error(
                f"[{name}] Failed to fully disable (close error): {e}\n{traceback.format_exc()}"
            )
        self._audit_log_event(name, "disabled", "Plugin disabled")

    def list_plugins(self) -> List[Dict[str, Any]]:
        """Return a list of all plugins and their status/manifest."""
        with self._registry_lock:
            return [
                {
                    "name": name,
                    "status": entry["status"],
                    "manifest": entry.get("manifest", {}),
                    "error": entry.get("error", ""),
                }
                for name, entry in self.registry.items()
            ]

    def get_plugin(self, name: str) -> Optional[Any]:
        """Return the plugin instance if available."""
        with self._registry_lock:
            entry = self.registry.get(name)
            if entry and entry.get("status") in ("loaded", "warning"):
                return entry.get("instance")
            return None

    async def health(self, name: str) -> Dict[str, Any]:
        """
        Check the health of a loaded plugin by calling its health check method.

        Args:
            name: The name of the plugin to check

        Returns:
            A dictionary with at least a 'status' key indicating health status
        """
        with self._registry_lock:
            if name not in self.registry:
                return {
                    "status": "error",
                    "message": f"Plugin '{name}' not found in registry",
                }

            entry = self.registry.get(name)
            if entry.get("status") not in ("loaded", "warning", "disabled"):
                return {
                    "status": "error",
                    "message": f"Plugin '{name}' is not in a checkable state (status: {entry.get('status')})",
                }

            instance = entry.get("instance")
            if not instance:
                return {
                    "status": "error",
                    "message": f"Plugin '{name}' has no instance",
                }

            manifest = entry.get("manifest", {})
            plugin_type = manifest.get("type")
            health_method_name = manifest.get("health_check")

        try:
            # For wrapper instances (WASM, gRPC, PythonSubprocessProxy)
            if hasattr(instance, "health") and callable(getattr(instance, "health")):
                health_fn = getattr(instance, "health")
                if asyncio.iscoroutinefunction(health_fn):
                    result = await health_fn()
                else:
                    # Run sync health check in thread to avoid blocking
                    result = await asyncio.to_thread(health_fn)
            # For in-process Python modules
            elif plugin_type == "python" and health_method_name:
                if hasattr(instance, health_method_name):
                    health_fn = getattr(instance, health_method_name)
                    if asyncio.iscoroutinefunction(health_fn):
                        result = await health_fn()
                    else:
                        # Run sync health check in thread
                        result = await asyncio.to_thread(health_fn)
                else:
                    return {
                        "status": "error",
                        "message": f"Health method '{health_method_name}' not found on plugin instance",
                    }
            else:
                return {
                    "status": "error",
                    "message": f"No health check available for plugin type '{plugin_type}'",
                }

            # Ensure result is a dict with at least a status
            if not isinstance(result, dict):
                result = {"status": "ok", "message": str(result)}
            elif "status" not in result:
                result["status"] = "ok"

            # Update Prometheus health metric
            if prometheus_available:
                health_value = (
                    1.0 if result.get("status") in ["ok", "serving", "healthy"] else 0.0
                )
                PLUGIN_HEALTH_STATUS.labels(plugin_name=name).set(health_value)

            return result
        except asyncio.TimeoutError:
            result = {
                "status": "error",
                "message": f"Health check timeout for plugin '{name}'",
            }
            if prometheus_available:
                PLUGIN_HEALTH_STATUS.labels(plugin_name=name).set(0.0)
            return result
        except Exception as e:
            plugin_logger.error(
                f"[{name}] Health check failed: {e}\n{traceback.format_exc()}"
            )
            result = {"status": "error", "message": f"Health check failed: {str(e)}"}
            if prometheus_available:
                PLUGIN_HEALTH_STATUS.labels(plugin_name=name).set(0.0)
                PLUGIN_ERRORS_TOTAL.labels(
                    error_type="health_check_failure", plugin_name=name
                ).inc()
            return result

    async def load_all(self, check_health: bool = False):
        """Discover and load (or register) all plugins in the plugins directory."""
        plugins_to_load = await self.discover_plugins()
        for plugin_path in plugins_to_load:
            self.load_plugin(plugin_path, check_health=check_health)

    async def close_all_plugins(self):
        """Gracefully close all loaded plugin runners (WASM, gRPC) and stop background loop."""
        # Snapshot what to close
        to_close: List[Tuple[str, Any, str]] = []
        with self._registry_lock:
            for name, entry in self.registry.items():
                instance = entry.get("instance")
                plugin_type = entry.get("manifest", {}).get("type")
                to_close.append((name, instance, plugin_type))

        for name, instance, plugin_type in to_close:
            try:
                if hasattr(instance, "close"):
                    if asyncio.iscoroutinefunction(getattr(instance, "close")):
                        plugin_logger.info(
                            f"[{name}] Closing plugin resources (async)."
                        )
                        await instance.close()  # type: ignore
                    else:
                        plugin_logger.info(f"[{name}] Closing plugin resources (sync).")
                        # Run sync close in thread to avoid blocking loop
                        await asyncio.to_thread(getattr(instance, "close"))
                elif plugin_type == "python":
                    # Nothing to close for process proxy
                    pass
                with self._registry_lock:
                    entry = self.registry.get(name, {})
                    entry["status"] = (
                        "closed" if plugin_type in HANDLERS else "unloaded"
                    )
                    entry["instance"] = None
            except Exception as e:
                plugin_logger.error(f"[{name}] Error during plugin close: {e}")
                with self._registry_lock:
                    entry = self.registry.get(name, {})
                    entry["status"] = "error_on_close"
                    entry["instance"] = None

        # Stop background loop after cleanup
        self.stop_background_loop()

    def _audit_log_event(self, plugin_name: str, event_type: str, details: str):
        """Internal method to log audit events for plugins using structured JSON format."""
        # Scrub sensitive data before logging (best effort)
        if detect_secrets_available:
            try:
                secrets = SecretsCollection()
                secrets.scan_string(details)
                if getattr(secrets, "json", lambda: None)():
                    details = "Sensitive data scrubbed."
            except Exception:
                pass

        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": event_type,
            "plugin": plugin_name,
            "details": details,
        }
        plugin_logger.info(json.dumps(log_entry))

    def _run_coro_blocking(self, coro: Awaitable[Any]) -> Any:
        """
        Run an async coroutine from sync code safely using a dedicated background loop.
        Ensure _ensure_background_loop has been called before.
        """
        self._ensure_background_loop()
        assert self._bg_loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return fut.result(
            timeout=max(HEALTH_TIMEOUT_SEC, 30.0)
        )  # allow longer for non-health ops

    def get_plugin_api_methods(self, name: str) -> Optional[List[str]]:
        """Returns a list of public API methods for a loaded plugin instance."""
        plugin = self.get_plugin(name)
        if plugin is None:
            return None

        # Process proxy exposes only generic methods
        if isinstance(plugin, PythonSubprocessProxy):
            return ["health", "close"]

        methods: List[str] = []
        for attr_name in dir(plugin):
            if attr_name.startswith("_"):
                continue
            try:
                attr = getattr(plugin, attr_name)
            except Exception:
                continue
            if callable(attr) and (inspect.isfunction(attr) or inspect.ismethod(attr)):
                methods.append(attr_name)

        # Common async handler methods
        for special in ("run", "health", "close"):
            if hasattr(plugin, special) and callable(getattr(plugin, special)):
                methods.append(special)

        return sorted(set(methods))

    def summary(self) -> List[Dict[str, Any]]:
        """Returns a list of plugin summary dicts (for dashboards, monitoring, etc)."""
        return self.list_plugins()


# --- Usage example (main function for local testing) ---
async def main():
    plugins_test_dir = Path("./test_plugins_temp")
    os.makedirs(plugins_test_dir, exist_ok=True)

    # Dummy Python Plugin (single-file)
    python_plugin_content = """
PLUGIN_MANIFEST = {
  "name": "python_example_plugin",
  "version": "1.0.0",
  "description": "A sample Python plugin.",
  "entrypoint": "plugin_health",
  "type": "python",
  "author": "test",
  "capabilities": ["test_cap"],
  "permissions": ["filesystem"],
  "dependencies": [],
  "min_core_version": "0.0.0",
  "max_core_version": "9.9.9",
  "health_check": "plugin_health",
  "api_version": "v1",
  "license": "MIT",
  "homepage": "",
  "tags": ["test"],
  "sandbox": {"enabled": False},
  "manifest_version": "2.0"
}

def plugin_health():
    return {"status": "ok", "message": "Python plugin is healthy!"}

class PLUGIN_API:
    def greet(self, name):
        return f"Hello from Python plugin, {name}!"
"""
    with open(
        plugins_test_dir / "python_example_plugin.py", "w", encoding="utf-8"
    ) as f:
        f.write(python_plugin_content)

    # Dummy WASM Plugin Directory (requires a .wasm file for WasmRunner)
    wasm_plugin_dir = plugins_test_dir / "wasm_example_plugin"
    os.makedirs(wasm_plugin_dir, exist_ok=True)
    wasm_manifest_content = """
{
  "name": "wasm_example_plugin",
  "version": "0.5.0",
  "description": "A sample WASM plugin.",
  "entrypoint": "wasm_example_plugin.wasm",
  "type": "wasm",
  "author": "test",
  "capabilities": ["compute"],
  "permissions": [],
  "dependencies": [],
  "min_core_version": "0.0.0",
  "max_core_version": "9.9.9",
  "health_check": "health_check",
  "api_version": "v1",
  "license": "Apache-2.0",
  "homepage": "",
  "tags": ["wasm", "performance"],
  "sandbox": {
    "enabled": true,
    "isolation": "wasm_sandbox",
    "resource_limits": {
      "memory": "64MB",
      "runtime_seconds": 5
    }
  },
  "manifest_version": "2.0"
}
"""
    with open(wasm_plugin_dir / "manifest.json", "w", encoding="utf-8") as f:
        f.write(wasm_manifest_content)
    with open(wasm_plugin_dir / "wasm_example_plugin.wasm", "wb") as f:
        f.write(b"\x00\x61\x73\x6d\x01\x00\x00\x00")

    # Dummy gRPC Plugin Directory
    grpc_plugin_dir = plugins_test_dir / "grpc_example_plugin"
    os.makedirs(grpc_plugin_dir, exist_ok=True)
    grpc_manifest_content = """
{
  "name": "grpc_example_plugin",
  "version": "0.1.0",
  "description": "A sample gRPC plugin.",
  "entrypoint": "grpc://localhost:50051",
  "type": "grpc",
  "author": "test",
  "capabilities": ["network_comm"],
  "permissions": ["network"],
  "dependencies": [],
  "min_core_version": "0.0.0",
  "max_core_version": "9.9.9",
  "health_check": "Health.Check",
  "api_version": "v1",
  "license": "MIT",
  "homepage": "",
  "tags": ["grpc", "external"],
  "sandbox": {"enabled": true},
  "manifest_version": "2.0"
}
"""
    with open(grpc_plugin_dir / "manifest.json", "w", encoding="utf-8") as f:
        f.write(grpc_manifest_content)

    # Python directory plugin with file entrypoint
    py_dir = plugins_test_dir / "python_dir_plugin"
    os.makedirs(py_dir, exist_ok=True)
    with open(py_dir / "main.py", "w", encoding="utf-8") as f:
        f.write(
            "def plugin_health():\n    return {'status':'ok','message':'dir plugin ok'}\n"
        )
    with open(py_dir / "manifest.json", "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "name": "python_dir_plugin",
                    "version": "1.0.0",
                    "description": "Dir Python plugin.",
                    "entrypoint": "main.py",
                    "type": "python",
                    "author": "test",
                    "capabilities": [],
                    "permissions": [],
                    "dependencies": [],
                    "min_core_version": "0.0.0",
                    "max_core_version": "9.9.9",
                    "health_check": "plugin_health",
                    "api_version": "v1",
                    "license": "MIT",
                    "homepage": "",
                    "tags": [],
                    "sandbox": {"enabled": False},
                    "manifest_version": "2.0",
                }
            )
        )

    pm = PluginManager(plugins_test_dir)
    await pm.load_all(check_health=True)

    print("\n--- Plugin Summary ---")
    for p in pm.summary():
        status = p["status"]
        name = p["name"]
        manifest = p["manifest"]
        error_msg = p["error"]
        print(f"Name: {name}")
        print(f"  Status: {status}")
        print(f"  Type: {manifest.get('type', '?')}")
        print(f"  Version: {manifest.get('version','?')}")
        print(f"  Capabilities: {manifest.get('capabilities','')}")
        if error_msg:
            print(f"    ERROR: {error_msg}")

        if status in ["loaded", "warning"]:
            health_status = await pm.health(name)
            print(
                f"  Health Check: {health_status['status']} - {health_status.get('message', '')}"
            )
        print("-" * 30)

    python_plugin = pm.get_plugin("python_example_plugin")
    if python_plugin and not isinstance(python_plugin, PythonSubprocessProxy):
        print(
            f"\nCalling Python plugin health (inproc): {await pm.health('python_example_plugin')}"
        )
        if hasattr(python_plugin, "PLUGIN_API"):
            print(
                f"Calling Python plugin greet: {python_plugin.PLUGIN_API().greet('Alice')}"
            )
        print(
            f"Python plugin API methods: {pm.get_plugin_api_methods('python_example_plugin')}"
        )
    else:
        print(
            "\nPython plugins are process-isolated; use manager.health() to check status."
        )

    if "wasm" in HANDLERS:
        wasm_plugin = pm.get_plugin("wasm_example_plugin")
        if wasm_plugin:
            print(
                f"\nCalling WASM plugin health: {await pm.health('wasm_example_plugin')}"
            )
            print(
                f"WASM plugin API methods: {pm.get_plugin_api_methods('wasm_example_plugin')}"
            )

    if "grpc" in HANDLERS:
        grpc_plugin = pm.get_plugin("grpc_example_plugin")
        if grpc_plugin:
            print(
                f"\nCalling gRPC plugin health: {await pm.health('grpc_example_plugin')}"
            )
            print(
                f"gRPC plugin API methods: {pm.get_plugin_api_methods('grpc_example_plugin')}"
            )

    print("\n--- Testing enable/disable for python_example_plugin ---")
    pm.disable_plugin("python_example_plugin")
    print(f"Status after disable: {pm.registry['python_example_plugin']['status']}")
    pm.enable_plugin("python_example_plugin")
    print(f"Status after enable: {pm.registry['python_example_plugin']['status']}")

    await pm.close_all_plugins()
    shutil.rmtree(plugins_test_dir, ignore_errors=True)
    plugin_logger.info("Example cleanup complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Operation interrupted by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
