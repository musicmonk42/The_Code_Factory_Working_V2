import os
import sys
import logging
import asyncio
import importlib
import pkg_resources
import pkgutil
import hashlib
import json
import traceback
import inspect
import re
import time
from typing import Dict, Any, List, Tuple, Protocol, Optional, runtime_checkable
from pathlib import Path

# --- Constants & Configuration ---
SIMULATION_PACKAGE = "simulation"
IS_DEMO_MODE = os.getenv("DEMO_MODE", "False").lower() == "true"
PLUGIN_TIMEOUT_SECONDS = float(os.getenv("PLUGIN_TIMEOUT_SECONDS", "30"))
REGISTRY_PLUGINS_PATH = os.getenv("REGISTRY_PLUGINS_PATH", f"{os.path.abspath(os.path.dirname(__file__))}/../plugins")
DEFAULT_ALLOWLIST: Dict[str, Dict[str, Any]] = {}  # Empty by default for security

# --- Logging Setup ---
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s'
)

# --- Audit Logging Setup ---
class AuditLogger:
    """Interface for audit logging."""
    async def emit_audit_event(self, kind: str, details: Dict[str, Any], severity: str):
        raise NotImplementedError

class FallbackAuditLogger(AuditLogger):
    """Fallback audit logger using standard logging."""
    async def emit_audit_event(self, kind: str, details: Dict[str, Any], severity: str):
        logger.log(
            getattr(logging, severity.upper(), logging.INFO),
            f"AUDIT_EVENT - {kind}: {json.dumps(details)}"
        )

class DltAuditLogger(AuditLogger):
    """DLT audit logger."""
    def __init__(self, emit_audit_event):
        self.emit_audit_event = emit_audit_event

    async def emit_audit_event(self, kind: str, details: Dict[str, Any], severity: str):
        try:
            await self.emit_audit_event(kind, details, severity)
        except Exception as e:
            logger.error(f"Failed to emit DLT audit event: {e}")
            await FallbackAuditLogger().emit_audit_event(kind, details, severity)

def get_audit_logger() -> AuditLogger:
    """Initialize audit logger with fallback."""
    try:
        module = importlib.import_module("test_generation.audit_log")
        return DltAuditLogger(module.emit_audit_event)
    except (ImportError, AttributeError):
        logger.warning("test_generation.audit_log not available. Using fallback audit logger.")
        return FallbackAuditLogger()

audit_logger = get_audit_logger()

# --- Metrics Setup ---
class MetricsProvider:
    """Interface for metrics collection."""
    def observe_load_duration(self, duration: float): pass
    def increment_error(self, operation: str): pass
    def set_success_rate(self, plugin: str, value: float): pass

class DummyMetricsProvider(MetricsProvider):
    """Dummy metrics provider."""
    pass

class PrometheusMetricsProvider(MetricsProvider):
    """Prometheus metrics provider."""
    def __init__(self):
        try:
            from prometheus_client import Counter, Gauge, Histogram
            self.registry_load_duration = Histogram(
                'registry_load_duration_seconds',
                'Time taken to discover and register plugins'
            )
            self.registry_errors_total = Counter(
                'registry_errors_total',
                'Total errors during registry operations',
                ['operation']
            )
            self.plugin_success_rate = Gauge(
                'plugin_success_rate',
                'Success rate of plugin execution',
                ['plugin']
            )
        except Exception as e:
            logger.error(f"Failed to initialize Prometheus metrics: {e}")
            raise

    def observe_load_duration(self, duration: float):
        try:
            self.registry_load_duration.observe(duration)
        except Exception as e:
            logger.error(f"Failed to observe load duration: {e}")

    def increment_error(self, operation: str):
        try:
            self.registry_errors_total.labels(operation=operation).inc()
        except Exception as e:
            logger.error(f"Failed to increment error counter: {e}")

    def set_success_rate(self, plugin: str, value: float):
        try:
            self.plugin_success_rate.labels(plugin=plugin).set(value)
        except Exception as e:
            logger.error(f"Failed to set success rate: {e}")

def get_metrics_provider() -> MetricsProvider:
    """Initialize metrics provider with fallback."""
    try:
        import prometheus_client
        return PrometheusMetricsProvider()
    except ImportError:
        logger.warning("prometheus_client not available. Using dummy metrics provider.")
        return DummyMetricsProvider()

metrics_provider = get_metrics_provider()

# --- Output Refinement Setup ---
class OutputRefiner:
    """Interface for output refinement."""
    async def refine(self, plugin_name: str, output: str) -> str:
        raise NotImplementedError

class NoOpOutputRefiner(OutputRefiner):
    """No-op output refiner."""
    async def refine(self, plugin_name: str, output: str) -> str:
        original_hash = hashlib.sha256(output.encode()).hexdigest()
        refined_hash = hashlib.sha256(output.encode()).hexdigest()
        await audit_logger.emit_audit_event(
            "plugin_output_refined",
            {
                "module": plugin_name,
                "original_hash": original_hash,
                "refined_hash": refined_hash,
                "model": "none"
            }
        )
        return output

class LangChainOutputRefiner(OutputRefiner):
    """LangChain-based output refiner."""
    def __init__(self, chat=None):
        self.chat = chat
        if self.chat is None:
            try:
                from langchain_openai import ChatOpenAI
                self.chat = ChatOpenAI(model="gpt-4o-mini", temperature=0.0)
            except Exception as e:
                # Handle the case where initialization fails (e.g., missing API key)
                logger.warning(f"Failed to initialize LangChain: {e}. Using fallback.")
                self.chat = None

    async def refine(self, plugin_name: str, output: str) -> str:
        if self.chat is None:
            return await NoOpOutputRefiner().refine(plugin_name, output)

        system_prompt = (
            f"You are a helpful assistant that refines output from the '{plugin_name}' plugin "
            "for human readability. Respond with a clean, formatted, human-friendly version. "
            "Be concise, focus on key findings, and return the text in a markdown code block."
        )
        try:
            from langchain_core.messages import HumanMessage
            refined_output = await self.chat.ainvoke([
                HumanMessage(content=f"{system_prompt}\n\nRaw Output:\n```\n{output}\n```")
            ])
            content = refined_output.content.strip()
            if content.startswith("```") and content.endswith("```"):
                content = content[3:-3].strip()
            original_hash = hashlib.sha256(output.encode()).hexdigest()
            refined_hash = hashlib.sha256(content.encode()).hexdigest()
            await audit_logger.emit_audit_event(
                "plugin_output_refined",
                {
                    "module": plugin_name,
                    "original_hash": original_hash,
                    "refined_hash": refined_hash,
                    "model": "gpt-4o-mini"
                }
            )
            return content
        except Exception as e:
            logger.error(f"Failed to refine plugin output for '{plugin_name}': {e}")
            await audit_logger.emit_audit_event(
                "plugin_refinement_failed",
                {
                    "module": plugin_name,
                    "error": str(e),
                    "traceback": traceback.format_exc()
                },
                severity="ERROR"
            )
            return output

def get_output_refiner() -> OutputRefiner:
    """Initialize output refiner with fallback."""
    refiner = LangChainOutputRefiner()
    if refiner.chat is None:
        logger.warning("LangChainOutputRefiner not initialized. Returning NoOpOutputRefiner.")
        return NoOpOutputRefiner()
    return refiner

output_refiner = get_output_refiner()

# --- Security and Sanitization ---
def generate_file_hash(file_path: str) -> str:
    """Generate a SHA256 hash of a file's contents."""
    try:
        hasher = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return f"sha256:{hasher.hexdigest()}"
    except (IOError, OSError) as e:
        logger.error(f"Failed to generate hash for {file_path}: {e}")
        return ""

def sanitize_path(path: str, root_dir: str) -> Optional[str]:
    """Ensure a path is safe and within a root directory."""
    try:
        abs_path = os.path.abspath(path)
        if not abs_path.startswith(os.path.abspath(root_dir)):
            logger.warning(f"Path {path} is outside root directory {root_dir}")
            return None
        return abs_path
    except (TypeError, ValueError) as e:
        logger.error(f"Invalid path {path}: {e}")
        return None

def redact_sensitive(text: str) -> str:
    """Redact sensitive information from a string."""
    patterns = {
        "api_key": r"(?:sk_|pk_)[a-zA-Z0-9]{5,}",
        "password": r"(password|pass|secret|token)\s*[:=]\s*([^\n\r,;&\s]+)",
        "credit_card": r"\b(?:\d{4}[- ]?){3}\d{4}\b"
    }
    for key, pattern in patterns.items():
        text = re.sub(pattern, f"[{key.upper()}_SCRUBBED]", text, flags=re.IGNORECASE)
    return text

# --- Plugin Manifest and Protocol ---
@runtime_checkable
class RunnerPlugin(Protocol):
    """Protocol for runner plugins."""
    async def run(self, target: str, params: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
        ...

@runtime_checkable
class DltClientPlugin(Protocol):
    """Protocol for DLT client plugins."""
    def some_dlt_method(self) -> str:
        ...

def validate_manifest(manifest: Dict[str, Any], module_name: str):
    """Validate a plugin's manifest."""
    required_keys = {"name", "version", "type"}
    if not required_keys.issubset(manifest.keys()):
        raise ValueError(f"Missing required keys in PLUGIN_MANIFEST for {module_name}. "
                         f"Required: {required_keys}, Found: {manifest.keys()}")

    valid_types = {"runner", "dlt_client", "siem_client", "other"}
    if manifest["type"] not in valid_types:
        raise ValueError(f"Invalid PLUGIN_MANIFEST type for {module_name}. "
                         f"Must be one of {valid_types}, Found: {manifest['type']}")

async def check_plugin_dependencies(manifest: Dict[str, Any], module_name: str) -> bool:
    """Check if all plugin dependencies are installed."""
    dependencies = manifest.get("dependencies", {})
    if not dependencies:
        return True

    try:
        pkg_resources.require([f"{pkg}{ver}" for pkg, ver in dependencies.items()])
        return True
    except pkg_resources.DistributionNotFound as e:
        dep_name = str(e.req) if hasattr(e, 'req') else str(e)
        required_version = str(e.req) if hasattr(e, 'req') else ""
        await audit_logger.emit_audit_event(
            "plugin_dependency_missing",
            {
                "module": module_name,
                "dependency": dep_name,
                "required_version": required_version,
                "error": "Dependency not found"
            },
            severity="ERROR"
        )
        return False
    except Exception as e:
        await audit_logger.emit_audit_event(
            "plugin_dependency_check_failed",
            {
                "module": module_name,
                "error": str(e),
                "traceback": traceback.format_exc()
            },
            severity="ERROR"
        )
        return False

# --- Registry ---
SIM_REGISTRY: Dict[str, Dict[str, Any]] = {
    "runners": {},
    "dlt_clients": {},
    "siem_clients": {},
    "other": {}
}

MODULE_ALLOWLIST: Dict[str, Dict[str, Any]] = {}
try:
    allowlist_path = os.path.join(os.path.dirname(__file__), "allowlist.json")
    with open(allowlist_path, "r") as f:
        MODULE_ALLOWLIST = json.load(f)
except FileNotFoundError:
    logger.warning("allowlist.json not found. Using empty allowlist for security.")
    MODULE_ALLOWLIST = {}
except Exception as e:
    logger.error(f"Failed to load allowlist.json: {e}")
    MODULE_ALLOWLIST = {}

def get_registry() -> Dict[str, Dict[str, Any]]:
    """Return the global plugin registry."""
    return SIM_REGISTRY

async def _is_allowed(module_name: str, module_path: Optional[str] = None) -> bool:
    """Check if a module is in the allowlist and verify its hash."""
    if module_name not in MODULE_ALLOWLIST:
        await audit_logger.emit_audit_event(
            "module_access_denied",
            {
                "name": module_name,
                "reason": "Not in allowlist",
                "demo_mode": IS_DEMO_MODE
            },
            severity="CRITICAL"
        )
        return False

    if module_path and MODULE_ALLOWLIST[module_name].get("expected_hash"):
        expected_hash = MODULE_ALLOWLIST[module_name]["expected_hash"]
        actual_hash = generate_file_hash(module_path)
        if actual_hash != expected_hash:
            await audit_logger.emit_audit_event(
                "module_tampering_detected",
                {
                    "name": module_name,
                    "path": module_path,
                    "expected_hash": expected_hash,
                    "actual_hash": actual_hash
                },
                severity="CRITICAL"
            )
            return False
    return True

async def register_plugin(module: Any, module_name: str, file_path: Optional[str]):
    """Validate and register a plugin module."""
    try:
        manifest = getattr(module, "PLUGIN_MANIFEST", None)
        if manifest is None:
            logger.warning(f"Skipping module '{module_name}': No PLUGIN_MANIFEST found.")
            await audit_logger.emit_audit_event(
                "module_registration_skipped",
                {"name": module_name, "reason": "No manifest"},
                severity="WARNING"
            )
            return

        validate_manifest(manifest, module_name)

        if not await check_plugin_dependencies(manifest, module_name):
            logger.error(f"Skipping plugin '{module_name}': Missing dependencies.")
            return

        plugin_type = manifest["type"]
        category = f"{plugin_type}s" if plugin_type in ["runner", "dlt_client", "siem_client"] else "other"

        run_attr = getattr(module, "run", None)
        if plugin_type == "runner":
            if not (asyncio.iscoroutinefunction(run_attr) or inspect.iscoroutinefunction(run_attr)):
                raise TypeError(f"Plugin '{module_name}' must implement the RunnerPlugin protocol.")

        SIM_REGISTRY[category][module_name] = module

        await audit_logger.emit_audit_event(
            f"{plugin_type}_registered",
            {"name": module_name, "manifest": manifest, "path": file_path}
        )
        logger.info(f"Registered plugin '{module_name}' of type '{plugin_type}'.")
    except (ValueError, TypeError, AttributeError) as e:
        logger.error(f"Failed to register plugin '{module_name}': {e}", exc_info=True)
        await audit_logger.emit_audit_event(
            "module_registration_failed",
            {
                "name": module_name,
                "error": str(e),
                "traceback": traceback.format_exc()
            },
            severity="ERROR"
        )
    except Exception as e:
        logger.error(f"Unexpected error registering plugin '{module_name}': {e}", exc_info=True)
        await audit_logger.emit_audit_event(
            "module_registration_failed",
            {
                "name": module_name,
                "error": "Unexpected error",
                "traceback": traceback.format_exc()
            },
            severity="CRITICAL"
        )

async def discover_and_register_all():
    """Discover and register all plugins in the plugins directory or simulation package."""
    start_time = time.perf_counter()
    plugins_root: Optional[Path] = None
    added_path: Optional[str] = None
    try:
        pkg_paths: List[str] = []
        if os.path.isdir(REGISTRY_PLUGINS_PATH):
            plugins_root = Path(REGISTRY_PLUGINS_PATH)
            pkg_paths = [str(plugins_root)]
            sys.path.insert(0, str(plugins_root))
            added_path = str(plugins_root)
        else:
            pkg = sys.modules.get(SIMULATION_PACKAGE)
            if pkg and hasattr(pkg, "__path__"):
                pkg_paths = list(pkg.__path__)
            else:
                logger.warning(f"Plugin directory not found and package '{SIMULATION_PACKAGE}' not available.")
                return

        for finder, name, ispkg in pkgutil.iter_modules(pkg_paths):
            file_path = f"{plugins_root}/{name}.py" if plugins_root else None
            if not await _is_allowed(name, file_path):
                logger.warning(f"Module '{name}' is not in the allowlist or failed hash verification.")
                continue

            try:
                module = importlib.import_module(name)
                await register_plugin(module, name, file_path)
            except ImportError as e:
                logger.error(f"Failed to import module '{name}': {e}")
                await audit_logger.emit_audit_event(
                    "module_registration_failed",
                    {"name": name, "error": str(e), "traceback": traceback.format_exc()},
                    severity="ERROR"
                )
            except Exception as e:
                logger.error(f"Unexpected error importing module '{name}': {e}", exc_info=True)
                await audit_logger.emit_audit_event(
                    "module_registration_failed",
                    {"name": name, "error": "Unexpected error", "traceback": traceback.format_exc()},
                    severity="CRITICAL"
                )
    finally:
        if added_path and added_path in sys.path:
            sys.path.remove(added_path)
        duration = time.perf_counter() - start_time
        try:
            metrics_provider.observe_load_duration(duration)
        except Exception as e:
            logger.debug(f"Failed to observe registry_load_duration metric: {e}")
        logger.info(f"Plugin discovery and registration completed in {duration:.3f} seconds.")

async def refine_plugin_output(plugin_name: str, output: str) -> str:
    """Use an LLM to refine plugin output for human readability."""
    return await output_refiner.refine(plugin_name, output)

async def run_plugin(plugin_name: str, target: str, params: Dict[str, Any]) -> Tuple[bool, str, Optional[str]]:
    """Execute a plugin with a given target and parameters."""
    plugin = SIM_REGISTRY["runners"].get(plugin_name)
    if not plugin:
        metrics_provider.increment_error("run_plugin")
        raise ValueError(f"Plugin '{plugin_name}' is not registered as a runner.")

    timeout = params.pop("timeout", PLUGIN_TIMEOUT_SECONDS)

    try:
        success, message, output = await asyncio.wait_for(
            plugin.run(target, params),
            timeout=timeout
        )
        final_output = redact_sensitive(output) if output else None
        await audit_logger.emit_audit_event(
            "plugin_execution",
            {
                "plugin": plugin_name,
                "target": target,
                "success": True,
                "output_hash": hashlib.sha256(final_output.encode()).hexdigest() if final_output else None
            }
        )
        metrics_provider.set_success_rate(plugin_name, 1)
        return success, message, final_output
    except asyncio.TimeoutError:
        metrics_provider.increment_error("run_plugin")
        message = f"Plugin '{plugin_name}' timed out after {timeout} seconds."
        logger.error(message)
        await audit_logger.emit_audit_event(
            "plugin_execution_timeout",
            {
                "plugin": plugin_name,
                "target": target,
                "timeout": timeout
            },
            severity="ERROR"
        )
        metrics_provider.set_success_rate(plugin_name, 0)
        return False, message, None
    except Exception as e:
        metrics_provider.increment_error("run_plugin")
        message = f"Plugin '{plugin_name}' execution failed: {e}"
        logger.error(message, exc_info=True)
        await audit_logger.emit_audit_event(
            "plugin_execution_failed",
            {
                "plugin": plugin_name,
                "target": target,
                "error": str(e),
                "traceback": traceback.format_exc()
            },
            severity="ERROR"
        )
        metrics_provider.set_success_rate(plugin_name, 0)
        return False, message, None

if __name__ == '__main__':
    async def main():
        await discover_and_register_all()
        if "test_runner_plugin" in SIM_REGISTRY["runners"]:
            success, message, output = await run_plugin("test_runner_plugin", "example.com", {})
            logger.info(f"Plugin Result: {success} - {message} - {output}")

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())