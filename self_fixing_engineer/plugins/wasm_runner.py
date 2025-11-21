import os
import json
import logging
import asyncio
import time
import hashlib
import sys
import hmac
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable, Tuple, Literal
from packaging import version

from omnicore_engine.plugin_registry import plugin, PlugInKind

# --- Global Production Mode Flag (from main orchestrator) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# --- Custom Exceptions for the WASM runner ---
class WasmRunnerError(Exception):
    """Base exception for WASM runner errors."""
    pass

class WasmStartupError(WasmRunnerError):
    """Exception for critical errors during WASM runner startup."""
    pass

class WasmExecutionError(WasmRunnerError):
    """Exception for errors during WASM function execution."""
    pass

class AnalyzerCriticalError(Exception):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    """
    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(message)
        alert_operator(message, alert_level)

class NonCriticalError(Exception):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """
    pass

# --- Centralized Utilities (replacing placeholders) ---
try:
    from core_utils import alert_operator, scrub_secrets as scrub_sensitive_data
    from core_audit import audit_logger
    from core_secrets import SECRETS_MANAGER
except ImportError as e:
    # Library code: do not sys.exit(); raise and let the orchestrator decide.
    raise WasmStartupError(f"Missing core dependency for WASM runner: {e}") from e

# --- Dependency Enforcement: Hard-fail startup if wasmtime or pydantic are missing. ---
try:
    import wasmtime
except ImportError as e:
    alert_operator(f"CRITICAL: wasmtime-py missing. WASM runner aborted.", level="CRITICAL")
    raise WasmStartupError(f"wasmtime-py is not installed: {e}") from e

try:
    from pydantic import BaseModel, ValidationError, Field, validator
except ImportError as e:
    alert_operator(f"CRITICAL: pydantic missing. WASM runner aborted.", level="CRITICAL")
    raise WasmStartupError(f"pydantic is not installed: {e}") from e

# --- Manifest Schema Validation (MANDATORY) ---
class WasmManifestModel(BaseModel):
    """A Pydantic model to validate the structure of a WASM plugin manifest."""
    name: str = Field(..., min_length=1)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    entrypoint: str = Field(..., min_length=1)  # Export to call as main (expects ABI below)
    type: Literal["wasm"]
    health_check: str = Field("plugin_health", min_length=1)
    api_version: str = Field("v1", min_length=1)
    min_core_version: str = Field("1.1.0", pattern=r"^\d+\.\d+\.\d+$")
    max_core_version: str = Field("2.0.0", pattern=r"^\d+\.\d+\.\d+$")

    # Resource Controls
    sandbox: Dict[str, Any] = Field(
        default_factory=lambda: {"enabled": True, "resource_limits": {"memory": "64MB", "runtime_seconds": 5, "network": False}}
    )

    capabilities: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    author: str = "Unknown"
    license: str = "Proprietary"
    homepage: Optional[str] = None
    description: str = "No description provided."
    is_demo_plugin: bool = False
    whitelisted_paths: List[str] = Field(default_factory=list)
    whitelisted_commands: List[str] = Field(default_factory=list)

    signature: Optional[str] = None

    @validator('name', 'entrypoint', 'health_check', 'api_version')
    def check_no_dummy_fields(cls, v):
        if PRODUCTION_MODE and ("dummy" in v.lower() or "test" in v.lower() or "mock" in v.lower()):
            raise ValueError(f"Dummy/test field value '{v}' detected in manifest. Not allowed in production.")
        return v

    @validator('sandbox')
    def validate_sandbox_config(cls, v):
        if not v.get("enabled", True):
            if PRODUCTION_MODE:
                raise ValueError("Sandbox must be enabled in PRODUCTION_MODE.")
            else:
                logger.warning("Sandbox disabled in manifest. Not recommended for non-prod.")
        resource_limits = v.get("resource_limits", {})
        if "memory" in resource_limits and (not isinstance(resource_limits["memory"], str) or "MB" not in resource_limits.get("memory", "")):
            raise ValueError("Memory limit must be a string like '128MB'.")
        if "runtime_seconds" in resource_limits and (not isinstance(resource_limits["runtime_seconds"], (int, float)) or resource_limits["runtime_seconds"] <= 0):
            raise ValueError("Runtime limit must be a positive number in seconds.")
        return v

    class Config:
        extra = "forbid"

# --- WASM Host Functions Security: Only expose host functions that are explicitly whitelisted ---
WHITELISTED_HOST_FUNCTIONS: Dict[str, Callable] = {
    "env.host_log": None,
}

def _is_in_allowlist(path: str, allowed_dirs: List[str]) -> bool:
    """
    Validates that a given path is a sub-path of an allowed directory,
    resolving symlinks to prevent path traversal attacks.
    """
    try:
        real_path = os.path.realpath(path)
        real_allowed_dirs = [os.path.realpath(d) for d in allowed_dirs]
        return any(os.path.commonpath([real_path, d]) == d for d in real_allowed_dirs)
    except OSError:
        return False

def _safe_exports_get(store: wasmtime.Store, instance: wasmtime.Instance, name: str, typ):
    exp = instance.exports(store).get(name)
    if not isinstance(exp, typ):
        raise WasmExecutionError(f"Required export '{name}' with type {typ.__name__} not found")
    return exp

def host_log_closure(runner_instance: "WasmRunner"):
    MAX_LOG_BYTES = 64 * 1024  # hard cap
    def _inner_host_log(store, ptr: int, length: int):
        try:
            mem = _safe_exports_get(store, runner_instance.instance, "memory", wasmtime.Memory)
            length_capped = min(int(length), MAX_LOG_BYTES)
            if ptr < 0 or ptr + length_capped > mem.data_len(store):
                raise WasmExecutionError("Memory read out of bounds.")
            data = mem.read(store, ptr, length_capped)
            message = data.decode("utf-8", "replace")
            if len(message) > 2048:
                message = message[:2035] + " ...(truncated)"
            logger.info(f"[WASM LOG] {message}")
            audit_logger.log_event("wasm_host_log_call", ptr=int(ptr), length=int(length), message=message)
        except Exception as e:
            logger.error(f"[WASM LOG] Failed to read log message from WASM: {e}")
            audit_logger.log_event("wasm_host_log_error", error=str(e))
    return _inner_host_log

# --- WASM Runner Class ---
class WasmRunner:
    """
    Manages loading, running, and health-checking of WebAssembly (WASM) plugins.
    Enforces memory and runtime limits.

    ABI contract enforced here:
      - Inputs: arguments are marshalled as JSON bytes and passed as (ptr, len) pairs.
      - Output: function returns two i32 values (out_ptr, out_len) pointing to a UTF-8 JSON buffer.
      - Module must export: memory, alloc(len)->ptr, and (optionally) free(ptr,len).
    """
    def __init__(self, plugin_name: str, manifest: Dict[str, Any], plugins_dir: str, whitelisted_plugin_dirs: List[str], core_version: str = "1.1.0"):
        # Synchronous validation; async I/O happens later.
        self.plugin_name = plugin_name
        self.plugins_dir = os.path.abspath(plugins_dir)
        self.whitelisted_plugin_dirs = [os.path.abspath(d) for d in whitelisted_plugin_dirs]
        self.core_version = core_version

        # Manifest Signature Validation (BLOCKER)
        self._validate_manifest_signature(manifest)

        try:
            self.manifest = WasmManifestModel(**manifest)
        except ValidationError as e:
            audit_logger.log_event("wasm_plugin_load_failure", plugin=plugin_name, reason="manifest_validation_failed", error=str(e))
            alert_operator(f"CRITICAL: Invalid WASM plugin manifest for {plugin_name}: {e}. Aborting.", level="CRITICAL")
            raise WasmStartupError(f"Invalid plugin manifest for {plugin_name}") from e

        if PRODUCTION_MODE and self.manifest.is_demo_plugin:
            audit_logger.log_event("wasm_plugin_load_failure", plugin=plugin_name, reason="demo_plugin_in_prod")
            alert_operator(f"CRITICAL: WASM plugin '{plugin_name}' detected in PRODUCTION_MODE. Aborting.", level="CRITICAL")
            raise WasmStartupError(f"Demo plugin '{plugin_name}' is forbidden in production.")

        if not _is_in_allowlist(self.plugins_dir, self.whitelisted_plugin_dirs):
            audit_logger.log_event("wasm_plugin_load_failure", plugin=plugin_name, reason="unwhitelisted_plugin_dir", dir=self.plugins_dir)
            alert_operator(f"CRITICAL: WASM plugin dir '{self.plugins_dir}' not whitelisted. Aborting.", level="CRITICAL")
            raise WasmStartupError(f"Plugin directory '{self.plugins_dir}' is not whitelisted.")

        self.wasm_filepath = os.path.join(self.plugins_dir, self.plugin_name, f"{self.plugin_name}.wasm")

        if not (version.parse(self.manifest.min_core_version) <= version.parse(self.core_version) <= version.parse(self.manifest.max_core_version)):
            audit_logger.log_event("wasm_plugin_load_failure", plugin=self.plugin_name, reason="core_version_incompatibility", plugin_version=self.manifest.version, core_version=self.core_version)
            alert_operator(f"CRITICAL: WASM plugin '{self.plugin_name}' incompatible with core version. Aborting.", level="CRITICAL")
            raise WasmStartupError(f"Plugin {self.plugin_name} is incompatible with core version {self.core_version}")

        # Configure wasmtime
        self.config = wasmtime.Config()
        self._setup_resource_limits_config()  # ONLY sets flags on self.config

        self.engine = wasmtime.Engine(self.config)
        self.store = wasmtime.Store(self.engine)
        self.linker = wasmtime.Linker(self.engine)
        self._define_host_functions()

        self.module: Optional[wasmtime.Module] = None
        self.instance: Optional[wasmtime.Instance] = None
        self.last_loaded_hash: Optional[str] = None
        self._call_lock = asyncio.Lock()

    def _validate_manifest_signature(self, manifest: Dict[str, Any]) -> None:
        """Validates the manifest integrity using HMAC signature."""
        data_to_hash = dict(manifest)
        sig = data_to_hash.pop("signature", None)
        key = SECRETS_MANAGER.get_secret("MANIFEST_HMAC_KEY", required=PRODUCTION_MODE)

        if PRODUCTION_MODE:
            if not key or not sig:
                raise WasmStartupError("Manifest signature required in PRODUCTION_MODE.")
            expect = hmac.new(key.encode(), json.dumps(data_to_hash, sort_keys=True).encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expect, sig):
                raise WasmStartupError("Manifest signature mismatch.")

    @classmethod
    async def create(cls, plugin_name: str, manifest: Dict[str, Any], plugins_dir: str, whitelisted_plugin_dirs: List[str]):
        """Async factory method to correctly initialize the runner."""
        instance = cls(plugin_name, manifest, plugins_dir, whitelisted_plugin_dirs)
        await instance._load_module_async()
        instance._instantiate_module()
        return instance

    def _setup_resource_limits_config(self) -> None:
        """Configure memory and runtime limits on the wasmtime.Config (no engine/store creation here)."""
        limits = self.manifest.sandbox.get("resource_limits", {})

        # Fuel-based runtime limiting if specified
        if limits.get("runtime_seconds"):
            try:
                self.config.consume_fuel = True  # Use direct attribute access
                logger.info(f"[{self.plugin_name}] Fuel-based runtime limiting enabled")
                audit_logger.log_event("wasm_resource_limit_set", plugin=self.plugin_name, resource="runtime_seconds", limit=limits["runtime_seconds"])
            except AttributeError as e:
                raise WasmStartupError(f"wasmtime version doesn't support fuel limiting: {e}") from e

        # Memory cap (best-effort; also validate module memories at load)
        mem = limits.get("memory")
        if mem:
            try:
                mb = int(mem.replace("MB", ""))
                max_pages = mb * 16  # 64 KiB pages
                # Guard set: not all versions expose these, so setattr
                try:
                    setattr(self.config, "static_memory_maximum", max_pages)
                    setattr(self.config, "static_memory_guard_size", 0)
                except Exception:
                    # If unsupported, rely solely on module memory validation below
                    pass
                logger.info(f"[{self.plugin_name}] Static memory cap configured (~{mb}MB)")
                audit_logger.log_event("wasm_resource_limit_set", plugin=self.plugin_name, resource="memory", limit=mem)
            except Exception as e:
                raise WasmStartupError(f"Invalid memory limit '{mem}': {e}") from e

    def _define_host_functions(self) -> None:
        """Only expose whitelisted host functions."""
        for capability in self.manifest.capabilities:
            if capability == "host_log":
                host_func_name = "env.host_log"
                if host_func_name in WHITELISTED_HOST_FUNCTIONS:
                    self.linker.define(
                        "env",
                        "host_log",
                        wasmtime.Func(self.store, wasmtime.FuncType([wasmtime.ValType.i32(), wasmtime.ValType.i32()], []), host_log_closure(self)),
                    )
                    audit_logger.log_event("wasm_host_function_defined", plugin=self.plugin_name, host_function=host_func_name)
                else:
                    raise WasmStartupError(f"Plugin {self.plugin_name} requests unwhitelisted host function '{host_func_name}'")

    def _validate_module_memory_constraints(self, module: wasmtime.Module) -> None:
        """Reject modules with unbounded or oversized memories vs. configured cap."""
        limits = self.manifest.sandbox.get("resource_limits", {})
        mem = limits.get("memory")
        if not mem:
            return
        cap_pages = int(mem.replace("MB", "")) * 16

        # Exports
        for et in module.exports:
            try:
                if isinstance(et.type, wasmtime.MemoryType):
                    mt = et.type
                    if mt.maximum is None or (mt.maximum is not None and mt.maximum > cap_pages):
                        raise WasmStartupError(f"Exported memory exceeds cap or is unbounded for plugin {self.plugin_name}")
            except AttributeError:
                # API compatibility: older wasmtime may wrap types differently
                pass

        # Imports
        for it in module.imports:
            try:
                if isinstance(it.type, wasmtime.MemoryType):
                    mt = it.type
                    if mt.maximum is None or (mt.maximum is not None and mt.maximum > cap_pages):
                        raise WasmStartupError(f"Imported memory exceeds cap or is unbounded for plugin {self.plugin_name}")
            except AttributeError:
                pass

    async def _load_module_async(self) -> None:
        """Loads the WASM module from the file system."""
        if not os.path.exists(self.wasm_filepath):
            raise WasmStartupError(f"WASM module not found: {self.wasm_filepath}")
        if not os.access(self.wasm_filepath, os.R_OK):
            raise WasmStartupError(f"No read access to WASM binary: {self.wasm_filepath}")
        if not _is_in_allowlist(self.wasm_filepath, self.whitelisted_plugin_dirs):
            raise WasmStartupError(f"WASM binary '{self.wasm_filepath}' is outside whitelisted directories.")

        file_hash = await asyncio.to_thread(lambda: hashlib.sha256(Path(self.wasm_filepath).read_bytes()).hexdigest())

        if self.last_loaded_hash == file_hash and self.module is not None:
            logger.debug(f"[{self.plugin_name}] WASM module unchanged, skipping reload.")
            return

        try:
            module = await asyncio.to_thread(wasmtime.Module.from_file, self.engine, self.wasm_filepath)
            # Memory constraints validation
            self._validate_module_memory_constraints(module)
            self.module = module
            self.last_loaded_hash = file_hash
            logger.info(f"[{self.plugin_name}] WASM module loaded from {self.wasm_filepath}")
            audit_logger.log_event("wasm_module_loaded", plugin=self.plugin_name, file=self.wasm_filepath, hash=file_hash)
        except wasmtime.WasmtimeError as e:
            raise WasmStartupError(f"Failed to load WASM: {e}") from e

    def _instantiate_module(self) -> None:
        """Instantiates the WASM module."""
        if not self.module:
            raise WasmExecutionError("WASM module not loaded.")
        try:
            self.instance = self.linker.instantiate(self.store, self.module)
            # Require essential exports early
            _safe_exports_get(self.store, self.instance, "memory", wasmtime.Memory)
            _safe_exports_get(self.store, self.instance, "alloc", wasmtime.Func)
            logger.info(f"[{self.plugin_name}] WASM module instantiated.")
            audit_logger.log_event("wasm_module_instantiated", plugin=self.plugin_name)
        except wasmtime.WasmtimeError as e:
            raise WasmStartupError(f"Failed to instantiate WASM: {e}") from e

    def _write_bytes(self, payload: bytes) -> Tuple[int, int]:
        """Write bytes to WASM memory using exported 'alloc'."""
        alloc = _safe_exports_get(self.store, self.instance, "alloc", wasmtime.Func)
        mem = _safe_exports_get(self.store, self.instance, "memory", wasmtime.Memory)
        ptr = alloc(self.store, len(payload))
        mem.write(self.store, ptr, payload)
        return int(ptr), int(len(payload))

    def _read_bytes(self, ptr: int, length: int, cap: int = 256 * 1024) -> bytes:
        """Safely read bytes from WASM memory with bounds checking and a max cap."""
        mem = _safe_exports_get(self.store, self.instance, "memory", wasmtime.Memory)
        ln = min(int(length), cap)
        if int(ptr) < 0 or int(ptr) + ln > mem.data_len(self.store):
            raise WasmExecutionError("WASM returned out-of-bounds buffer")
        return mem.read(self.store, int(ptr), ln)

    async def run_function(self, func_name: str, *args: Any) -> Any:
        """
        Runs an exported WASM function with JSON serializable arguments and returns JSON serializable result.

        ABI:
          call(func_name, *(ptr,len pairs for each arg))
          -> returns (out_ptr:int, out_len:int) for a UTF-8 JSON buffer.
        """
        async with self._call_lock:
            if not self.instance:
                raise WasmExecutionError(f"WASM module for {self.plugin_name} not instantiated.")

            audit_logger.log_event(
                "wasm_function_run_start",
                plugin=self.plugin_name,
                function=func_name,
                args_summary=scrub_sensitive_data(str(args)[:200])
            )
            t0 = time.time()

            limits = self.manifest.sandbox.get("resource_limits", {})
            runtime_limit_seconds = limits.get("runtime_seconds")

            # Per-call fuel budget if enabled
            if runtime_limit_seconds and getattr(self.config, "consume_fuel", False):
                try:
                    # Optional: attempt to drain leftover fuel if API supports it
                    # (Some versions expose fuel_remaining/consume_fuel; guard if absent)
                    if hasattr(self.store, "add_fuel"):
                        # Fuel budget calculation: 1_000_000 fuel units per second
                        # This is a tunable multiplier based on wasmtime's fuel consumption rate.
                        # Wasmtime fuel is consumed at approximately 1 unit per simple instruction.
                        # Adjust this multiplier based on your specific WASM module's complexity.
                        fuel_budget = int(float(runtime_limit_seconds) * 1_000_000)
                        self.store.add_fuel(fuel_budget)
                except Exception as e:
                    logger.warning(f"[{self.plugin_name}] Unable to add fuel: {e}")

            try:
                exported_func = _safe_exports_get(self.store, self.instance, func_name, wasmtime.Func)

                # Marshal args: JSON -> bytes -> (ptr, len) pairs
                processed: List[int] = []
                for arg in args:
                    if isinstance(arg, (bytes, bytearray)):
                        data = bytes(arg)
                    elif isinstance(arg, str):
                        data = arg.encode("utf-8")
                    else:
                        # JSON-serialize dict/list/primitive
                        data = json.dumps(arg).encode("utf-8")
                    ptr, ln = self._write_bytes(data)
                    processed.extend([ptr, ln])

                # Call and expect (out_ptr, out_len)
                def _call():
                    return exported_func(self.store, *processed)

                result = await asyncio.to_thread(_call)

                # Normalize: wasmtime returns tuple or list or ints
                if isinstance(result, (tuple, list)) and len(result) == 2 and all(isinstance(x, int) for x in result):
                    out_ptr, out_len = int(result[0]), int(result[1])
                elif isinstance(result, int):
                    # Some ABIs return only ptr; not supported here
                    raise WasmExecutionError("WASM returned single int; expected (ptr,len)")
                else:
                    raise WasmExecutionError("WASM did not return (ptr,len)")

                raw = self._read_bytes(out_ptr, out_len)

                # Optional free
                try:
                    free = self.instance.exports(self.store).get("free")
                    if isinstance(free, wasmtime.Func):
                        free(self.store, out_ptr, out_len)
                except Exception:
                    pass

                # Try JSON; fallback to UTF-8 text
                try:
                    return json.loads(raw.decode("utf-8"))
                except Exception:
                    return raw.decode("utf-8", "replace")

            except wasmtime.Trap as t:
                msg = str(t)
                if "all fuel consumed" in msg:
                    raise WasmExecutionError(f"runtime limit exceeded ({runtime_limit_seconds}s)") from t
                raise WasmExecutionError(f"WASM execution trapped: {msg}") from t
            except Exception as e:
                raise WasmExecutionError(f"Error during WASM function '{func_name}' call: {e}") from e
            finally:
                audit_logger.log_event("wasm_function_run_end", plugin=self.plugin_name, function=func_name, duration=time.time() - t0)

    async def plugin_health(self) -> Dict[str, Any]:
        """Performs a health check on the WASM plugin."""
        health_check_method = self.manifest.health_check
        if not health_check_method:
            logger.warning(f"[{self.plugin_name}] No health_check method defined in manifest.")
            return {"status": "warning", "message": "No health_check method defined in manifest."}

        try:
            health_result = await self.run_function(health_check_method)
            if isinstance(health_result, dict) and "status" in health_result:
                status = str(health_result.get("status", "unknown")).lower()
                if status in ("ok", "healthy"):
                    return {"status": "ok", "message": "Plugin is healthy.", "details": health_result}
                else:
                    logger.error(f"[{self.plugin_name}] Health check returned non-ok status: {status}. Details: {health_result}")
                    return {"status": "fail", "message": f"Plugin reported {status} status.", "details": health_result}
            else:
                logger.error(f"[{self.plugin_name}] Health check returned unexpected format: {type(health_result).__name__}")
                return {"status": "error", "message": "Health check returned unexpected format."}
        except Exception as e:
            logger.critical(f"CRITICAL: [{self.plugin_name}] Health check failed unexpectedly: {e}.", exc_info=True)
            return {"status": "error", "message": f"Health check failed unexpectedly: {e}"}

    async def reload_if_changed(self, operator_approved: bool = False):
        """Hot reload module if file has changed, with a concurrency lock."""
        if PRODUCTION_MODE and not operator_approved:
            raise WasmRunnerError("Hot-reload forbidden without operator approval in production.")

        async with self._call_lock:
            current_hash = hashlib.sha256(Path(self.wasm_filepath).read_bytes()).hexdigest()
            if current_hash != self.last_loaded_hash:
                logger.info(f"Hot-reloading plugin {self.plugin_name} (file changed).")
                audit_logger.log_event("wasm_plugin_reload_start", plugin=self.plugin_name, old_hash=self.last_loaded_hash, new_hash=current_hash, operator_approved=operator_approved)
                await self._load_module_async()
                self._instantiate_module()
                logger.info(f"WASM plugin {self.plugin_name} hot-reloaded successfully.")
                audit_logger.log_event("wasm_plugin_reload_success", plugin=self.plugin_name)
                return True
            logger.debug(f"Plugin {self.plugin_name} WASM file unchanged. No reload needed.")
            return False

    def close(self) -> None:
        """Cleans up WASM runtime resources."""
        logger.info(f"[{self.plugin_name}] WasmRunner closed.")
        audit_logger.log_event("wasm_runner_closed", plugin=self.plugin_name)

# --- CLI Tooling ---
def _validate_manifest_signature_dict(manifest: Dict[str, Any]) -> None:
    """Reusable signature validator for list/docs flows (enforced in PRODUCTION_MODE)."""
    data_to_hash = dict(manifest)
    sig = data_to_hash.pop("signature", None)
    key = SECRETS_MANAGER.get_secret("MANIFEST_HMAC_KEY", required=PRODUCTION_MODE)
    if PRODUCTION_MODE:
        if not key or not sig:
            raise WasmRunnerError("Manifest signature required in PRODUCTION_MODE.")
        expect = hmac.new(key.encode(), json.dumps(data_to_hash, sort_keys=True).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            raise WasmRunnerError("Manifest signature mismatch.")

def list_plugins(plugins_dir: str, whitelisted_plugin_dirs: List[str]):
    """Lists and validates all plugin manifest files in a given directory."""
    abs_plugins_dir = os.path.abspath(plugins_dir)
    if not _is_in_allowlist(abs_plugins_dir, whitelisted_plugin_dirs):
        raise WasmRunnerError(f"Listing plugins from non-whitelisted directory {abs_plugins_dir} is forbidden.")

    if not os.path.exists(abs_plugins_dir) or not os.path.isdir(abs_plugins_dir):
        logger.warning(f"Plugins directory not found: {abs_plugins_dir}")
        return []

    available_plugins = []
    for plugin_name in os.listdir(abs_plugins_dir):
        plugin_folder = os.path.join(abs_plugins_dir, plugin_name)
        manifest_file = os.path.join(plugin_folder, "manifest.json")
        if os.path.isdir(plugin_folder) and os.path.exists(manifest_file):
            try:
                with open(manifest_file, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
                _validate_manifest_signature_dict(manifest)
                WasmManifestModel(**manifest)
                available_plugins.append(plugin_name)
            except Exception as e:
                logger.warning(f"Skipping invalid plugin manifest for {plugin_name}: {e}")
    logger.info(f"Available WASM plugins in {abs_plugins_dir}: {available_plugins}")
    return available_plugins

def _allow_out_path(out_file: str, allowed_dirs: List[str]) -> None:
    """Ensure the output file is inside an allowed directory."""
    if not _is_in_allowlist(out_file, allowed_dirs):
        raise WasmRunnerError(f"Output path not allowed: {out_file}")

def generate_plugin_docs(plugins_dir: str, whitelisted_plugin_dirs: List[str], out_file: str = "WASM_PLUGIN_DOCS.md"):
    """Generates documentation for all valid WASM plugins."""
    abs_plugins_dir = os.path.abspath(plugins_dir)
    if not _is_in_allowlist(abs_plugins_dir, whitelisted_plugin_dirs):
        raise WasmRunnerError(f"Generating docs from non-whitelisted directory {abs_plugins_dir} is forbidden.")

    out_file = os.path.abspath(out_file)
    _allow_out_path(out_file, whitelisted_plugin_dirs)

    doc_lines = ["# WASM Plugins Documentation\n"]
    valid_plugins = list_plugins(plugins_dir, whitelisted_plugin_dirs)

    for plugin_name in valid_plugins:
        manifest_path = os.path.join(plugins_dir, plugin_name, "manifest.json")
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
            _validate_manifest_signature_dict(manifest)
            validated_manifest = WasmManifestModel(**manifest)

            doc_lines.append(f"## {validated_manifest.name}\n")
            doc_lines.append(f"- **Version:** {validated_manifest.version}")
            doc_lines.append(f"- **Description:** {validated_manifest.description}\n")
            doc_lines.append(f"- **Entrypoint:** `{validated_manifest.entrypoint}`")
            doc_lines.append(f"- **Health Check Function:** `{validated_manifest.health_check}`")
            doc_lines.append(f"- **API Version:** `{validated_manifest.api_version}`")
            doc_lines.append(f"- **Core Compatibility:** `{validated_manifest.min_core_version}` - `{validated_manifest.max_core_version}`")
            doc_lines.append(f"- **Capabilities:** {', '.join(validated_manifest.capabilities) or 'None'}")
            doc_lines.append(f"- **Tags:** {', '.join(validated_manifest.tags) or 'None'}")
            doc_lines.append(f"- **Author:** {validated_manifest.author}")
            doc_lines.append(f"- **License:** {validated_manifest.license}")
            doc_lines.append(f"- **Homepage:** {validated_manifest.homepage or 'N/A'}\n")

            doc_lines.append("### Sandbox Configuration:\n")
            doc_lines.append(f"- **Enabled:** {validated_manifest.sandbox.get('enabled', True)}")
            resource_limits = validated_manifest.sandbox.get('resource_limits', {})
            doc_lines.append(f"- **Memory Limit:** {resource_limits.get('memory', 'N/A')}")
            doc_lines.append(f"- **Runtime Limit:** {resource_limits.get('runtime_seconds', 'N/A')} seconds")
            doc_lines.append(f"- **Network Access:** {'Enabled' if resource_limits.get('network', True) else 'Disabled'}\n")

            doc_lines.append("### Approved Paths & Commands:\n")
            doc_lines.append(f"- **Whitelisted Paths:** {', '.join(validated_manifest.whitelisted_paths) or 'None'}")
            doc_lines.append(f"- **Whitelisted Commands:** {', '.join(validated_manifest.whitelisted_commands) or 'None'}\n")

            doc_lines.append("\n---\n")

        except Exception as e:
            logger.error(f"Error processing manifest for documentation {plugin_name}: {e}", exc_info=True)
            audit_logger.log_event("wasm_doc_gen_error", plugin_name=plugin_name, error=str(e))

    try:
        Path(out_file).write_text("\n".join(doc_lines), encoding='utf-8')
        logger.info(f"Generated WASM plugin documentation to {out_file}")
        audit_logger.log_event("wasm_doc_gen_success", file=out_file, plugin_count=len(valid_plugins))
    except OSError as e:
        logger.error(f"Failed to write documentation to {out_file}: {e}", exc_info=True)
        alert_operator(f"ERROR: Failed to write WASM documentation to {out_file}: {e}.", level="ERROR")
        raise IOError(f"Failed to write documentation to {out_file}: {e}") from e

# Optional: simple local test harness preserved (will fail in PRODUCTION_MODE with is_demo_plugin=True)
async def main_test():
    plugins_base_dir = "./test_plugins_wasm_temp"

    if os.path.exists(plugins_base_dir):
        shutil.rmtree(plugins_base_dir)
    os.makedirs(plugins_base_dir)

    plugin_name = "demo_wasm_plugin"
    plugin_dir = os.path.join(plugins_base_dir, plugin_name)
    os.makedirs(plugin_dir, exist_ok=True)

    manifest_content = {
        "name": plugin_name,
        "version": "0.0.1",
        "description": "A demo WASM plugin for testing.",
        "entrypoint": "main",
        "type": "wasm",
        "author": "Test",
        "capabilities": ["compute", "host_log"],
        "min_core_version": "1.0.0",
        "max_core_version": "2.0.0",
        "health_check": "health_check",
        "api_version": "v1",
        "license": "MIT",
        "homepage": "https://example.com/wasm_plugin",
        "tags": ["demo", "wasm"],
        "is_demo_plugin": True,
        "whitelisted_paths": ["/tmp/wasm_data"],
        "whitelisted_commands": ["echo"],
        "sandbox": {
            "enabled": True,
            "resource_limits": {
                "memory": "64MB",
                "runtime_seconds": 5,
                "network": False
            }
        },
    }
    manifest_path = os.path.join(plugin_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest_content, f, indent=2)
    logger.info(f"Generated dummy manifest at: {manifest_path}")

    dummy_wasm_path = os.path.join(plugin_dir, f"{plugin_name}.wasm")
    if not os.path.exists(dummy_wasm_path):
        with open(dummy_wasm_path, "wb") as f:
            f.write(b'\x00\x61\x73\x6d\x01\x00\x00\x00')
        logger.warning(f"\n--- WARNING: Dummy WASM file created at {dummy_wasm_path} ---")
        logger.warning("For full functionality, replace it with a compiled WASM module.")
    else:
        logger.info(f"Using existing WASM file: {dummy_wasm_path}")

    runner = None
    try:
        whitelisted_dirs = [os.path.abspath(plugins_base_dir)]
        runner = await WasmRunner.create(plugin_name, manifest_content, plugins_base_dir, whitelisted_dirs)

        health = await runner.plugin_health()
        logger.info(f"Plugin Health Check: {health}")

        with open(dummy_wasm_path, "ab") as f:
            f.write(b'changed')
        if await runner.reload_if_changed(operator_approved=True):
            logger.info("WASM plugin hot-reloaded successfully.")
            health_after_reload = await runner.plugin_health()
            logger.info(f"Plugin Health Check after reload: {health_after_reload}")

        generate_plugin_docs(plugins_base_dir, whitelisted_dirs, out_file=os.path.join(plugins_base_dir, "WASM_PLUGIN_DOCS.md"))

    except WasmRunnerError as e:
        logger.error(f"WASM Runner Error in main: {e}")
    except FileNotFoundError as e:
        logger.error(f"File Error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred in main: {e}", exc_info=True)
    finally:
        if runner:
            runner.close()
        if os.path.exists(plugins_base_dir):
            shutil.rmtree(plugins_base_dir)
        logger.info("Example cleanup complete.")

if __name__ == "__main__":
    asyncio.run(main_test())
