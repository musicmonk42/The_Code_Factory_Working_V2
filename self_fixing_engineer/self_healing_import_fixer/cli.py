#!/usr/bin/env python3
"""
cli.py - The All-Powerful Conductor for Code Analysis and Healing

This is the all-powerful, user-facing entry point for the entire Import Graph & Healing Suite.
It conducts and orchestrates all analysis, healing, and visualization tasks with a focus on
ergonomics, CI/CD integration, and infinite extensibility via a robust plugin architecture.

Conductor Features:
- Interactive, Atomic, Extensible: Every subcommand is atomic, transactional, and can be
  invoked from the CLI, CI pipelines, APIs, or IDEs. Interactive mode supports staged
  approvals and full undo capabilities.
- Full Dashboard Integration: The CLI can launch, monitor, and interact with the live web
  dashboard, including support for webhook-style triggers for re-analysis.
- Advanced Plugin and Hook Engine: A full-fledged plugin system allows for the addition of new
  commands, reports, or healing strategies. Hooks can be registered to run at any lifecycle stage.
- Comprehensive Auditing and Undo: All actions are tracked in a comprehensive, exportable audit
  log. Reverting changes is always possible via script or a dedicated git branch.
- Seamless IDE/CI Integration: Designed from the ground up for machine consumption with structured
  JSON output, automated CI detection, and hooks for linters, formatters, and testers.

Example usage:
  python cli.py analyze . --output-format json
  python cli.py heal src --fix-cycles --interactive
  python cli.py serve myproj --port 8000
  python cli.py --list-plugins
  python cli.py selftest
"""

import argparse
import sys
import logging
import importlib
import asyncio
from collections import defaultdict
from typing import Dict, Any, Optional, Callable, List
from pathlib import Path
import yaml
import os
import json
import traceback
import tempfile  # For selftest dummy file creation
import shutil  # For path validation and cleanup
import hmac  # For signature verification
import hashlib  # For signature verification

# --- Python Version Enforcement ---
REQUIRED_PYTHON = (3, 10)
if sys.version_info < REQUIRED_PYTHON:
    sys.stderr.write(
        f"ERROR: Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]} or higher is required.\n"
    )
    sys.exit(1)

# --- Robust import context for both package and "file-loaded" execution ---
import sys


def _bootstrap_import_paths():
    """
    Ensure we can import sibling packages whether this file is executed as part of the
    installed package (self_healing_import_fixer) or loaded directly via spec_from_file_location.
    """
    here = Path(__file__).resolve()
    # Potential roots that might contain our subpackages
    candidates = [
        here.parent,  # .../self_healing_import_fixer/
        here.parent.parent,  # .../ (repo root that contains self_healing_import_fixer/)
    ]
    for c in candidates:
        # If the conventional package layout exists, prefer that
        if (c / "self_healing_import_fixer" / "import_fixer").exists() and str(
            c
        ) not in sys.path:
            sys.path.insert(0, str(c))
            break
        # Or if running from within the package dir already (no outer package folder)
        if (c / "import_fixer").exists() and str(c) not in sys.path:
            sys.path.insert(0, str(c))
            break


_bootstrap_import_paths()

# Now import compat/core modules with fallbacks for both contexts
try:
    # Preferred absolute (installed package)
    from self_healing_import_fixer.import_fixer.compat_core import (
        PRODUCTION_MODE,
        cli_audit_logger,
        alert_operator,
        scrub_secrets,
        get_core_dependencies,
        load_analyzer,
    )
    from self_healing_import_fixer.analyzer.graph import ImportGraphAnalyzer
except ModuleNotFoundError:
    # Bare package (when executed inside package dir)
    try:
        from import_fixer.compat_core import (
            PRODUCTION_MODE,
            cli_audit_logger,
            alert_operator,
            scrub_secrets,
            get_core_dependencies,
            load_analyzer,
        )
        from analyzer.graph import ImportGraphAnalyzer
    except ModuleNotFoundError:
        # Last chance: after sys.path bootstrapping, try again
        from import_fixer.compat_core import (
            PRODUCTION_MODE,
            cli_audit_logger,
            alert_operator,
            scrub_secrets,
            load_analyzer,
        )
        from analyzer.graph import ImportGraphAnalyzer

__version__ = "4.0.0"

PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"


# --- Centralized Utilities & Logging ---
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


logger = logging.getLogger("import_suite_cli")


class AnalyzerCriticalError(Exception):
    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(message)
        alert_operator(message, alert_level)


class NonCriticalError(Exception):
    pass


# --- Centralized Utilities and Audit Logging (prod-safe via compat layer) ---
from import_fixer.compat_core import (
    alert_operator,  # real core in prod, safe fallback elsewhere
    scrub_secrets,
    SECRETS_MANAGER,
    audit_logger,  # unified logger (exposes .info/.error/.log_event)
)


def is_ci_environment() -> bool:
    return any(
        os.environ.get(key)
        for key in ["CI", "GITHUB_ACTIONS", "JENKINS_URL", "GITLAB_CI"]
    )


def setup_logging(verbose: bool, log_format: str) -> None:
    is_ci = is_ci_environment()
    log_level = logging.DEBUG if verbose else logging.INFO
    final_format = "json" if is_ci or log_format == "json" else "text"
    logger.setLevel(log_level)
    handler = logging.StreamHandler(sys.stdout)
    formatter = (
        JsonFormatter()
        if final_format == "json"
        else logging.Formatter("%(asctime)s - %(levelname)s: %(message)s")
    )
    handler.setFormatter(formatter)
    if logger.hasHandlers():
        logger.handlers.clear()
    logger.addHandler(handler)
    if is_ci:
        logger.info(
            "CI environment detected. Adjusting logging defaults.",
            extra={"cli_mode": "ci"},
        )
    if PRODUCTION_MODE:
        logger.info("Running in PRODUCTION_MODE.", extra={"cli_mode": "production"})


# --- Audit Log Path Validation ---
def _validate_output_path(path: str):
    abs_path = os.path.abspath(path)
    if os.path.islink(abs_path):
        raise AnalyzerCriticalError(
            f"Audit log path {abs_path} is a symlink. Aborting for safety."
        )
    if not os.path.isdir(os.path.dirname(abs_path)):
        raise AnalyzerCriticalError(
            f"Audit log directory does not exist for {abs_path}"
        )
    if os.path.exists(abs_path) and not os.access(abs_path, os.W_OK):
        raise AnalyzerCriticalError(f"No write access to audit log at {abs_path}")
    return abs_path


cli_audit_logger = audit_logger

# --- Plugin & Hook System ---
PLUGIN_SIGNATURE_KEY_ENV = "CLI_PLUGIN_SIGNATURE_KEY"
_plugin_signature_key: Optional[bytes] = None


def _get_plugin_signature_key() -> bytes:
    global _plugin_signature_key
    if _plugin_signature_key is None:
        key_str = SECRETS_MANAGER.get_secret(
            PLUGIN_SIGNATURE_KEY_ENV, required=True if PRODUCTION_MODE else False
        )
        if not key_str:
            if PRODUCTION_MODE:
                raise AnalyzerCriticalError(
                    "PLUGIN_SIGNATURE_KEY not set in PRODUCTION_MODE. Plugin verification is mandatory. Aborting startup."
                )
            else:
                logger.warning(
                    "PLUGIN_SIGNATURE_KEY not set. Plugin verification will be skipped (INSECURE)."
                )
                _plugin_signature_key = os.urandom(32).hex().encode("utf-8")
        else:
            _plugin_signature_key = key_str.encode("utf-8")
    return _plugin_signature_key


def _verify_plugin_integrity(file_path: str, expected_signature: str) -> bool:
    """Verifies the HMAC signature of a plugin file."""
    if not os.path.exists(file_path):
        logger.error(f"Plugin file not found for integrity check: {file_path}")
        return False
    try:
        with open(file_path, "rb") as f:
            file_content = f.read()
        h = hmac.new(_get_plugin_signature_key(), file_content, hashlib.sha256)
        calculated_signature = h.hexdigest()
        if hmac.compare_digest(calculated_signature, expected_signature):
            logger.debug(f"Plugin integrity verified for {file_path}.")
            return True
        else:
            logger.error(
                f"Plugin integrity MISMATCH for {file_path}. Calculated: {calculated_signature}, Expected: {expected_signature}. Possible tampering detected!"
            )
            cli_audit_logger.log_event(
                "plugin_integrity_mismatch",
                file=file_path,
                calculated=calculated_signature,
                expected=expected_signature,
            )
            alert_operator(
                f"CRITICAL: Plugin integrity mismatch for {file_path}. Possible tampering! Aborting.",
                level="CRITICAL",
            )
            return False
    except Exception as e:
        logger.error(
            f"Error during plugin integrity verification for {file_path}: {e}",
            exc_info=True,
        )
        cli_audit_logger.log_event(
            "plugin_integrity_error", file=file_path, error=str(e)
        )
        alert_operator(
            f"CRITICAL: Plugin integrity verification failed for {file_path}: {e}. Aborting.",
            level="CRITICAL",
        )
        return False


class PluginManager:
    """Discovers, loads, and executes plugins and hooks."""

    def __init__(
        self,
        plugin_dirs: Optional[List[str]] = None,
        approved_plugins: Optional[Dict[str, str]] = None,
    ):
        self.plugins: Dict[str, Any] = {}
        self.plugin_dirs = (
            plugin_dirs
            if plugin_dirs is not None
            else [os.path.join(Path(__file__).parent, "plugins")]
        )
        self.approved_plugins = approved_plugins if approved_plugins is not None else {}
        self.docs: Dict[str, str] = {}
        self.examples: Dict[str, List[str]] = {}
        self.hooks: Dict[str, List[Callable]] = defaultdict(list)
        self.whitelisted_plugin_dirs: List[str] = [
            os.path.abspath(d) for d in self.plugin_dirs
        ]
        if PRODUCTION_MODE and not self.whitelisted_plugin_dirs:
            logger.critical(
                "CRITICAL: In PRODUCTION_MODE, 'whitelisted_plugin_dirs' must be configured for PluginManager. Aborting startup."
            )
            alert_operator(
                "CRITICAL: PluginManager: No whitelisted plugin directories configured in PRODUCTION_MODE. Aborting.",
                level="CRITICAL",
            )
            sys.exit(1)
        self.plugin_versions: Dict[str, str] = {}  # For versioning

    async def _load_plugin_file_async(
        self, full_plugin_path: str, parser: argparse.ArgumentParser
    ):
        module_name = Path(full_plugin_path).stem
        if module_name in self.plugins:
            return
        cli_audit_logger.log_event(
            "plugin_load_attempt",
            module_name=module_name,
            path=scrub_secrets(full_plugin_path),
        )
        if PRODUCTION_MODE:
            expected_signature = self.approved_plugins.get(module_name)
            if not expected_signature:
                logger.critical(
                    f"CRITICAL: Plugin '{module_name}' from '{full_plugin_path}' is not in approved_plugins list or has no expected signature. Aborting startup."
                )
                cli_audit_logger.log_event(
                    "plugin_load_forbidden",
                    file=full_plugin_path,
                    reason="not_approved_or_unsigned",
                )
                alert_operator(
                    f"CRITICAL: Unapproved/unsigned plugin '{module_name}' found. Aborting.",
                    level="CRITICAL",
                )
                sys.exit(1)
            if not await asyncio.to_thread(
                _verify_plugin_integrity, full_plugin_path, expected_signature
            ):
                logger.critical(
                    f"CRITICAL: Plugin '{module_name}' integrity verification failed. Aborting startup."
                )
                sys.exit(1)
        try:
            spec = importlib.util.spec_from_file_location(module_name, full_plugin_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                await asyncio.to_thread(spec.loader.exec_module, module)
                self.plugins[module_name] = module
                self.docs[module_name] = getattr(module, "__doc__", None)
                self.examples[module_name] = getattr(module, "EXAMPLES", [])
                self.plugin_versions[module_name] = getattr(
                    module, "__version__", "unknown"
                )
                if hasattr(module, "register_command"):
                    try:
                        await asyncio.to_thread(module.register_command, parser)
                        logger.debug(f"Plugin '{module_name}' registered its command.")
                        cli_audit_logger.log_event(
                            "plugin_loaded",
                            module_name=module_name,
                            path=full_plugin_path,
                            status="success",
                        )
                    except Exception as e:
                        logger.critical(
                            f"CRITICAL: Plugin '{module_name}' failed to register command: {e}. Aborting startup.",
                            exc_info=True,
                        )
                        cli_audit_logger.log_event(
                            "plugin_load_failure",
                            module_name=module_name,
                            path=full_plugin_path,
                            reason="command_registration_failed",
                            error=str(e),
                        )
                        alert_operator(
                            f"CRITICAL: Plugin '{module_name}' failed to register command. Aborting.",
                            level="CRITICAL",
                        )
                        sys.exit(1)
        except Exception as e:
            logger.critical(
                f"CRITICAL: Failed to load plugin '{module_name}' from '{full_plugin_path}': {e}. Aborting startup.",
                exc_info=True,
            )
            cli_audit_logger.log_event(
                "plugin_load_failure",
                module_name=module_name,
                path=full_plugin_path,
                reason="import_failed",
                error=str(e),
            )
            alert_operator(
                f"CRITICAL: Failed to load plugin '{module_name}'. Aborting.",
                level="CRITICAL",
            )
            sys.exit(1)

    async def discover_and_load(self, parser: argparse.ArgumentParser):
        cli_audit_logger.log_event(
            "plugin_discovery_start", plugin_dirs=self.plugin_dirs
        )
        plugin_files_to_load = []
        for plugin_dir in self.plugin_dirs:
            abs_plugin_dir = os.path.abspath(plugin_dir)
            if PRODUCTION_MODE and not any(
                abs_plugin_dir.startswith(d) for d in self.whitelisted_plugin_dirs
            ):
                logger.critical(
                    f"CRITICAL: Attempted to load plugins from non-whitelisted directory: {abs_plugin_dir}. Aborting startup."
                )
                cli_audit_logger.log_event(
                    "plugin_load_forbidden",
                    dir=abs_plugin_dir,
                    reason="not_whitelisted",
                    whitelisted_dirs=self.whitelisted_plugin_dirs,
                )
                alert_operator(
                    f"CRITICAL: Plugin load from non-whitelisted dir '{abs_plugin_dir}' forbidden in PRODUCTION_MODE. Aborting.",
                    level="CRITICAL",
                )
                sys.exit(1)
            if not os.path.isdir(abs_plugin_dir):
                logger.warning(
                    f"Plugin directory '{abs_plugin_dir}' does not exist. Skipping."
                )
                cli_audit_logger.log_event(
                    "plugin_load_skipped",
                    dir=abs_plugin_dir,
                    reason="directory_not_found",
                )
                continue
            for f in os.listdir(abs_plugin_dir):
                if f.endswith(".py") and not f.startswith("_"):
                    full_plugin_path = os.path.join(abs_plugin_dir, f)
                    plugin_files_to_load.append(full_plugin_path)
        # Parallelize plugin loading with timeouts for robustness
        tasks = [
            asyncio.wait_for(self._load_plugin_file_async(p, parser), timeout=10)
            for p in plugin_files_to_load
        ]
        await asyncio.gather(*tasks, return_exceptions=True)
        cli_audit_logger.log_event(
            "plugin_discovery_complete",
            loaded_plugins=list(self.plugins.keys()),
            plugin_versions=self.plugin_versions,
        )

    def run_hook(self, hook_name: str, *args, **kwargs):
        logger.debug(f"Running hook: {hook_name}")
        cli_audit_logger.log_event("hook_run_start", hook_name=hook_name)
        for name, plugin in list(self.plugins.items()):
            if hasattr(plugin, hook_name):
                try:
                    scrubbed_args = scrub_secrets(args)
                    scrubbed_kwargs = scrub_secrets(kwargs)
                    getattr(plugin, hook_name)(*scrubbed_args, **scrubbed_kwargs)
                    cli_audit_logger.log_event(
                        "hook_run_success", hook_name=hook_name, plugin=name
                    )
                except Exception as e:
                    logger.critical(
                        f"CRITICAL: Error in hook '{hook_name}' for plugin '{name}': {e}. Unloading plugin and aborting.",
                        exc_info=True,
                    )
                    cli_audit_logger.log_event(
                        "hook_run_failure",
                        hook_name=hook_name,
                        plugin=name,
                        error=str(e),
                    )
                    del self.plugins[name]
                    alert_operator(
                        f"CRITICAL: Plugin hook '{hook_name}' failed for '{name}' and was unloaded. Aborting.",
                        level="CRITICAL",
                    )
                    sys.exit(1)

    def list_plugins(self, log_format="text"):
        plugins_list = [
            {
                "name": name,
                "doc": doc or "(no docstring)",
                "examples": self.examples.get(name, []),
                "version": self.plugin_versions.get(name, "unknown"),
            }
            for name, doc in self.docs.items()
        ]
        if is_ci_environment() or log_format == "json":
            print(json.dumps(plugins_list, indent=2))
            cli_audit_logger.log_event(
                "list_plugins_command", format=log_format, count=len(plugins_list)
            )
            return
        try:
            from rich.console import Console
            from rich.table import Table
        except ImportError:
            logger.error(
                "The 'rich' library is required for text-based plugin listings. Please install it."
            )
            return
        console = Console()
        table = Table(title="Installed Plugins")
        table.add_column("Name")
        table.add_column("Version")
        table.add_column("Description")
        table.add_column("Examples")
        for p in plugins_list:
            examples = "\n".join(p.get("examples", []))
            table.add_row(
                p["name"],
                p.get("version", "unknown"),
                p["doc"],
                examples or "(no examples)",
            )
        console.print(table)
        cli_audit_logger.log_event(
            "list_plugins_command", format=log_format, count=len(self.docs)
        )


# --- Module Loaders (with Dependency Gating) ---
def load_analyzer():
    global ImportGraphAnalyzer
    if ImportGraphAnalyzer is None:
        try:
            from import_fixer.analyzer import (
                ImportGraphAnalyzer as ActualImportGraphAnalyzer,
            )

            ImportGraphAnalyzer = ActualImportGraphAnalyzer
            logger.debug("Analyzer module loaded.")
            cli_audit_logger.log_event(
                "module_load", module="analyzer", status="success"
            )
        except ImportError as e:
            logger.critical(
                f"CRITICAL: Analyzer module not found: {e}. Aborting startup.",
                exc_info=True,
            )
            cli_audit_logger.log_event(
                "module_load", module="analyzer", status="failure", error=str(e)
            )
            alert_operator(
                "CRITICAL: Analyzer module missing. Aborting.", level="CRITICAL"
            )
            sys.exit(1)


def load_fixer():
    global heal_entrypoint
    if heal_entrypoint is None:
        try:
            from import_fixer.fixer import main as actual_heal_entrypoint

            heal_entrypoint = actual_heal_entrypoint
            logger.debug("Fixer module loaded.")
            cli_audit_logger.log_event("module_load", module="fixer", status="success")
        except ImportError as e:
            logger.critical(
                f"CRITICAL: Fixer module not found: {e}. Aborting startup.",
                exc_info=True,
            )
            cli_audit_logger.log_event(
                "module_load", module="fixer", status="failure", error=str(e)
            )
            alert_operator(
                "CRITICAL: Fixer module missing. Aborting.", level="CRITICAL"
            )
            sys.exit(1)


def load_requests():
    global requests
    if requests is None:
        try:
            import requests as actual_requests

            requests = actual_requests
            logger.debug("Requests module loaded.")
            cli_audit_logger.log_event(
                "module_load", module="requests", status="success"
            )
        except ImportError as e:
            logger.critical(
                f"CRITICAL: 'requests' library not found: {e}. Required for network operations. Aborting startup.",
                exc_info=True,
            )
            cli_audit_logger.log_event(
                "module_load", module="requests", status="failure", error=str(e)
            )
            alert_operator(
                "CRITICAL: 'requests' library missing. Aborting.", level="CRITICAL"
            )
            sys.exit(1)


# --- Config Management ---
def load_config(config_path: str) -> Dict[str, Any]:
    if not config_path:
        logger.info("No config file specified. Using default settings.")
        return {}
    config_file_abs_path = os.path.abspath(config_path)
    _validate_output_path(config_file_abs_path)
    if not os.path.exists(config_file_abs_path):
        logger.critical(
            f"CRITICAL: Configuration file not found at: {config_file_abs_path}. Aborting startup."
        )
        cli_audit_logger.log_event(
            "config_load_failure", path=config_file_abs_path, reason="file_not_found"
        )
        alert_operator(
            f"CRITICAL: Config file not found: {config_file_abs_path}. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
    try:
        with open(config_file_abs_path, "r", encoding="utf-8") as f:
            if config_file_abs_path.endswith((".json", ".JSON")):
                config_data = json.load(f)
            elif config_file_abs_path.endswith((".yaml", ".yml")):
                config_data = yaml.safe_load(f)
            else:
                logger.critical(
                    f"CRITICAL: Unsupported configuration file format: {config_file_abs_path}. Must be .json or .yaml. Aborting startup."
                )
                cli_audit_logger.log_event(
                    "config_load_failure",
                    path=config_file_abs_path,
                    reason="unsupported_format",
                )
                alert_operator(
                    f"CRITICAL: Config file has unsupported format: {config_file_abs_path}. Aborting.",
                    level="CRITICAL",
                )
                sys.exit(1)
        if not isinstance(config_data, dict):
            raise ValueError("Configuration content must be a dictionary.")
        logger.info(f"Configuration loaded from {config_file_abs_path}.")
        cli_audit_logger.log_event(
            "config_loaded",
            path=config_file_abs_path,
            config_keys=list(config_data.keys()),
        )
        return config_data
    except Exception as e:
        logger.critical(
            f"CRITICAL: Failed to load config {config_file_abs_path}: {e}. Aborting startup.",
            exc_info=True,
        )
        cli_audit_logger.log_event(
            "config_load_failure",
            path=config_file_abs_path,
            reason="parsing_error",
            error=str(e),
        )
        alert_operator(
            f"CRITICAL: Failed to load config {config_file_abs_path}: {e}. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)


class CustomArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(f"Error: {message}\n\n")
        self.print_help()
        cli_audit_logger.log_event("cli_parsing_error", message=message)
        sys.exit(2)


# --- Path Validation Helper ---
def _validate_path_argument(
    path: str,
    arg_name: str,
    is_dir: bool = True,
    allow_symlink: bool = False,
    allowlist: Optional[List[str]] = None,
) -> str:
    abs_path = os.path.abspath(path)
    if ".." in path.split(os.sep):
        logger.critical(
            f"CRITICAL: Path traversal attempt detected in '{arg_name}' argument: {path}. Aborting."
        )
        cli_audit_logger.log_event(
            "security_violation", type="path_traversal", arg_name=arg_name, path=path
        )
        alert_operator(
            f"CRITICAL: Path traversal detected in CLI arg '{arg_name}'. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
    if not allow_symlink and os.path.islink(abs_path):
        logger.critical(
            f"CRITICAL: Symlink detected for '{arg_name}' argument: {path}. Symlinks are forbidden. Aborting."
        )
        cli_audit_logger.log_event(
            "security_violation", type="symlink_forbidden", arg_name=arg_name, path=path
        )
        alert_operator(
            f"CRITICAL: Symlink detected for CLI arg '{arg_name}'. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
    if is_dir and not os.path.isdir(abs_path):
        logger.critical(
            f"CRITICAL: Directory specified for '{arg_name}' does not exist or is not a directory: {path}. Aborting."
        )
        cli_audit_logger.log_event(
            "path_validation_failure",
            type="dir_not_found",
            arg_name=arg_name,
            path=path,
        )
        alert_operator(
            f"CRITICAL: Directory for CLI arg '{arg_name}' not found. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
    elif not is_dir and not os.path.exists(abs_path):
        logger.critical(
            f"CRITICAL: File specified for '{arg_name}' does not exist: {path}. Aborting."
        )
        cli_audit_logger.log_event(
            "path_validation_failure",
            type="file_not_found",
            arg_name=arg_name,
            path=path,
        )
        alert_operator(
            f"CRITICAL: File for CLI arg '{arg_name}' not found. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
    # --- ENFORCE PROJECT SCOPE/ALLOWLIST ---
    project_scope = allowlist or [os.getcwd()]
    if not any(abs_path.startswith(os.path.abspath(scope)) for scope in project_scope):
        logger.critical(
            f"CRITICAL: Path '{path}' for '{arg_name}' is outside project scope/allowlist. Aborting."
        )
        cli_audit_logger.log_event(
            "security_violation",
            type="path_outside_scope",
            arg_name=arg_name,
            path=path,
            allowlist=project_scope,
        )
        alert_operator(
            f"CRITICAL: Path '{path}' for CLI arg '{arg_name}' is outside project scope. Aborting.",
            level="CRITICAL",
        )
        sys.exit(1)
    if not os.access(abs_path, os.R_OK):
        raise AnalyzerCriticalError(f"No read access to {abs_path} for {arg_name}")
    return abs_path


def create_parser(plugin_manager: PluginManager) -> CustomArgumentParser:
    parser = CustomArgumentParser(
        description="The All-Powerful Conductor for Code Analysis and Healing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose, debug-level logging.",
    )
    parser.add_argument(
        "--config", help="Path to a YAML config file for global settings."
    )
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default="text",
        help="Set log output format.",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Force CI mode for structured output and defaults.",
    )
    parser.add_argument(
        "--list-plugins",
        action="store_true",
        help="List all installed plugins and their docs/examples.",
    )
    parser.add_argument(
        "--list-hooks", action="store_true", help="List all registered hooks."
    )
    parser.add_argument(
        "--undo",
        type=int,
        help="Undo last [n] change sets (requires audit log or git).",
    )
    parser.add_argument(
        "--plugins-dir",
        help="Directory to auto-load plugins from. Can be specified multiple times.",
        action="append",
    )
    subparsers = parser.add_subparsers(
        dest="command", title="commands", help="Available commands", required=True
    )
    analyze_parser = subparsers.add_parser(
        "analyze", help="Perform deep analysis of import and call graphs."
    )
    analyze_parser.add_argument(
        "root", help="The root directory of the Python project to analyze."
    )
    analyze_parser.add_argument(
        "--output-format",
        choices=["text", "json-ide", "markdown", "html"],
        default="text",
        help="Format for analysis output.",
    )
    analyze_parser.add_argument(
        "--no-suggestions", action="store_true", help="Omit cycle fix suggestions."
    )
    analyze_parser.add_argument(
        "--ai-suggestions",
        action="store_true",
        help="Enable AI-powered refactoring suggestions.",
    )
    analyze_parser.add_argument(
        "--graph-type",
        choices=["import", "call"],
        default="import",
        help="Type of dependency graph to analyze.",
    )
    analyze_parser.add_argument(
        "--include-function-imports",
        action="store_true",
        help="Include imports inside functions.",
    )

    heal_parser = subparsers.add_parser(
        "heal", help="Automatically fix import statements and project structure."
    )
    heal_parser.add_argument(
        "roots", nargs="+", help="One or more project root directories to heal."
    )
    heal_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show proposed changes without applying them.",
    )
    heal_parser.add_argument(
        "--fix-cycles",
        action="store_true",
        help="Attempt to automatically resolve circular import dependencies.",
    )
    heal_parser.add_argument(
        "--fix-deps",
        action="store_true",
        help="Find and add missing dependencies to pyproject.toml and requirements.txt.",
    )
    heal_parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Prompt for confirmation before applying each change.",
    )
    heal_parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Run a test suite after applying fixes to validate them.",
    )
    heal_parser.add_argument(
        "--use-git-branch",
        action="store_true",
        help="Create a git branch for changes instead of .bak files.",
    )
    heal_parser.add_argument(
        "--pre-hook", help="A shell command to run before the healing process."
    )
    heal_parser.add_argument(
        "--post-hook", help="A shell command to run after the healing process."
    )
    heal_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Auto-approve all changes (non-interactive).",
    )
    heal_parser.add_argument(
        "--backup",
        action="store_true",
        help="Create .bak files for backups (even with git).",
    )
    heal_parser.add_argument(
        "--create-pr",
        action="store_true",
        help="Create a pull request after changes (requires git and gh).",
    )
    heal_parser.add_argument(
        "--python-version",
        default=f"{sys.version_info.major}.{sys.version_info.minor}",
        help="Python version for stdlib detection (e.g., '3.9').",
    )

    visualize_parser = subparsers.add_parser(
        "visualize", help="Export the dependency graph to various formats."
    )
    visualize_parser.add_argument(
        "root", help="The root directory of the Python project."
    )
    visualize_parser.add_argument(
        "format",
        choices=["dot", "json", "mermaid", "graphml"],
        help="The desired output format.",
    )
    visualize_parser.add_argument(
        "output_file", help="The path to write the output file to."
    )
    visualize_parser.add_argument(
        "--graph-type",
        choices=["import", "call"],
        default="import",
        help="Type of graph to export.",
    )

    serve_parser = subparsers.add_parser(
        "serve", help="Serve an interactive web dashboard for analysis."
    )
    serve_parser.add_argument("root", help="The project root directory to analyze.")
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="Host for the dashboard server."
    )
    serve_parser.add_argument(
        "--port", type=int, default=5000, help="Port for the dashboard server."
    )
    serve_parser.add_argument(
        "--require-auth",
        action="store_true",
        help="Require authentication for dashboard access.",
    )
    serve_parser.add_argument(
        "--https-cert", help="Path to HTTPS certificate for secure serving."
    )
    serve_parser.add_argument(
        "--https-key", help="Path to HTTPS private key for secure serving."
    )

    trigger_parser = subparsers.add_parser(
        "trigger", help="Send a webhook-style trigger to a running dashboard."
    )
    trigger_parser.add_argument(
        "event", choices=["rescan"], help="The event to trigger."
    )
    trigger_parser.add_argument(
        "--target",
        default="http://127.0.0.1:5000/api/trigger",
        help="The target dashboard API URL.",
    )

    selftest_parser = subparsers.add_parser(
        "selftest",
        help="Run self-diagnostic and report health of the CLI suite, plugins, and core engine.",
    )
    return parser


async def main_async():
    temp_parser = argparse.ArgumentParser(add_help=False)
    temp_parser.add_argument("--plugins-dir", help=argparse.SUPPRESS, action="append")
    temp_parser.add_argument(
        "--production-mode", action="store_true", help=argparse.SUPPRESS
    )
    temp_args, remaining_args = temp_parser.parse_known_args()
    global PRODUCTION_MODE
    if temp_args.production_mode:
        PRODUCTION_MODE = True
        os.environ["PRODUCTION_MODE"] = "true"
        logger.info("PRODUCTION_MODE forced ON via CLI flag.")
    elif PRODUCTION_MODE:
        logger.info("Running in PRODUCTION_MODE.")
    else:
        logger.info("Running in development/non-production mode.")

    global_config = {}
    if "--config" in remaining_args:
        config_idx = remaining_args.index("--config")
        if config_idx + 1 < len(remaining_args):
            global_config = load_config(remaining_args[config_idx + 1])
    plugin_manager = PluginManager(
        plugin_dirs=temp_args.plugins_dir,
        approved_plugins=global_config.get("plugins", {}).get("approved_plugins", {}),
    )
    parser = create_parser(plugin_manager)
    await plugin_manager.discover_and_load(parser)
    try:
        import argcomplete

        argcomplete.autocomplete(parser)
    except ImportError:
        pass
    args = parser.parse_args(remaining_args)
    setup_logging(args.verbose, args.log_format)
    cli_audit_logger.log_event(
        "cli_start", cli_args=scrub_secrets(vars(args)), production_mode=PRODUCTION_MODE
    )
    allowlist = plugin_manager.whitelisted_plugin_dirs
    if args.config:
        _validate_path_argument(
            args.config, "config", is_dir=False, allowlist=allowlist
        )
    if args.plugins_dir:
        for p_dir in args.plugins_dir:
            _validate_path_argument(
                p_dir, "plugins-dir", is_dir=True, allowlist=allowlist
            )
    if PRODUCTION_MODE and os.getenv("PRODUCTION_MODE", "false").lower() != "true":
        logger.critical(
            "CRITICAL: PRODUCTION_MODE is ON but not set via environment variable. Aborting for consistency."
        )
        alert_operator(
            "CRITICAL: PRODUCTION_MODE mismatch. Aborting.", level="CRITICAL"
        )
        sys.exit(1)
    try:
        plugin_manager.run_hook(
            "pre_command_execution", args=args, config=global_config
        )
        if getattr(args, "list_plugins", False):
            plugin_manager.list_plugins(log_format=args.log_format)
            cli_audit_logger.log_event(
                "command_executed",
                command="list_plugins",
                args=scrub_secrets(vars(args)),
            )
            sys.exit(0)
        if getattr(args, "list_hooks", False):
            for hook_name, funcs in plugin_manager.hooks.items():
                print(f"Hook '{hook_name}':")
                for func in funcs:
                    print(f"  - {func.__name__} from {func.__module__}")
            cli_audit_logger.log_event(
                "command_executed", command="list_hooks", args=scrub_secrets(vars(args))
            )
            sys.exit(0)
        if args.command == "analyze":
            root_path = _validate_path_argument(
                args.root, "root", is_dir=True, allowlist=allowlist
            )
            load_analyzer()
            analyzer = ImportGraphAnalyzer(
                root_path, config=global_config.get("analyzer", {})
            )
            if args.output_format == "json-ide":
                report = {
                    "cycles": analyzer.detect_cycles(),
                    "dead_nodes": analyzer.detect_dead_nodes(),
                }
                print(json.dumps(scrub_secrets(report), indent=2))
            elif args.output_format == "text":
                print(analyzer.generate_text_report())
            elif args.output_format == "markdown":
                print(analyzer.generate_markdown_report())
            elif args.output_format == "html":
                print(analyzer.generate_html_report())
            else:
                logger.error("Unknown report format requested.")
            cli_audit_logger.log_event(
                "command_executed", command="analyze", args=scrub_secrets(vars(args))
            )
        elif args.command == "heal":
            for root_path in args.roots:
                _validate_path_argument(
                    root_path, "roots", is_dir=True, allowlist=allowlist
                )
            if PRODUCTION_MODE and not args.dry_run and not args.interactive:
                logger.critical(
                    "CRITICAL: In PRODUCTION_MODE, 'heal' command requires '--interactive' flag for explicit operator approval when not in dry-run. Aborting."
                )
                cli_audit_logger.log_event(
                    "command_forbidden", command="heal", reason="no_interactive_in_prod"
                )
                alert_operator(
                    "CRITICAL: 'heal' command requires '--interactive' in PRODUCTION_MODE. Aborting.",
                    level="CRITICAL",
                )
                sys.exit(1)
            if PRODUCTION_MODE and args.yes:
                logger.critical(
                    "CRITICAL: '--yes' flag is forbidden in PRODUCTION_MODE for 'heal' command. Aborting."
                )
                cli_audit_logger.log_event(
                    "command_forbidden", command="heal", reason="yes_forbidden_in_prod"
                )
                alert_operator(
                    "CRITICAL: '--yes' flag forbidden in PRODUCTION_MODE. Aborting.",
                    level="CRITICAL",
                )
                sys.exit(1)
            load_fixer()
            fixer_config = global_config.get("fixer", {})
            fixer_config["whitelisted_paths"] = allowlist
            heal_entrypoint(args, config=fixer_config)
            cli_audit_logger.log_event(
                "command_executed", command="heal", args=scrub_secrets(vars(args))
            )
        elif args.command == "visualize":
            root_path = _validate_path_argument(
                args.root, "root", is_dir=True, allowlist=allowlist
            )
            output_file = _validate_path_argument(
                args.output_file, "output_file", is_dir=False, allowlist=allowlist
            )
            load_analyzer()
            analyzer = ImportGraphAnalyzer(
                root_path, config=global_config.get("analyzer", {})
            )
            export_map = {
                "dot": analyzer.visualize_graph,
                "json": analyzer.export_json,
                "mermaid": analyzer.export_mermaid,
                "graphml": analyzer.export_graphml,
            }
            export_func = export_map[args.format]
            if args.format == "dot":
                analyzer.visualize_graph(output_file=args.output_file, format="dot")
            else:
                export_func(args.output_file, args.graph_type)
            logger.info(f"{args.format.upper()} graph written to {args.output_file}")
            cli_audit_logger.log_event(
                "command_executed", command="visualize", args=scrub_secrets(vars(args))
            )
        elif args.command == "serve":
            root_path = _validate_path_argument(
                args.root, "root", is_dir=True, allowlist=allowlist
            )

            # Load the analyzer module BEFORE production security gates so tests (and ops)
            # can verify it initializes even when we abort on missing flags.
            load_analyzer()

            if PRODUCTION_MODE:
                if not args.require_auth:
                    logger.critical(
                        "CRITICAL: In PRODUCTION_MODE, 'serve' command requires '--require-auth'. Aborting."
                    )
                    cli_audit_logger.log_event(
                        "command_forbidden",
                        command="serve",
                        reason="auth_not_required_in_prod",
                    )
                    alert_operator(
                        "CRITICAL: Dashboard requires authentication in PRODUCTION_MODE. Aborting.",
                        level="CRITICAL",
                    )
                    sys.exit(1)
                if not args.https_cert or not args.https_key:
                    logger.critical(
                        "CRITICAL: In PRODUCTION_MODE, 'serve' command requires '--https-cert' and '--https-key' for HTTPS. Aborting."
                    )
                    cli_audit_logger.log_event(
                        "command_forbidden",
                        command="serve",
                        reason="https_not_enforced_in_prod",
                    )
                    alert_operator(
                        "CRITICAL: Dashboard requires HTTPS in PRODUCTION_MODE. Aborting.",
                        level="CRITICAL",
                    )
                    sys.exit(1)
                if args.host == "0.0.0.0" or args.host == "::":
                    logger.critical(
                        "CRITICAL: In PRODUCTION_MODE, 'serve' command cannot bind to public interfaces (0.0.0.0 or ::). Aborting."
                    )
                    cli_audit_logger.log_event(
                        "command_forbidden",
                        command="serve",
                        reason="public_bind_forbidden_in_prod",
                    )
                    alert_operator(
                        "CRITICAL: Dashboard cannot bind to public interfaces in PRODUCTION_MODE. Aborting.",
                        level="CRITICAL",
                    )
                    sys.exit(1)

            # Only construct and run the analyzer after passing security checks.
            analyzer = ImportGraphAnalyzer(
                root_path, config=global_config.get("analyzer", {})
            )
            analyzer.serve_dashboard(
                args.host,
                args.port,
                require_auth=args.require_auth,
                https_cert=args.https_cert,
                https_key=args.https_key,
            )
            cli_audit_logger.log_event(
                "command_executed", command="serve", args=scrub_secrets(vars(args))
            )
        elif args.command == "trigger":
            load_requests()
            payload = {"event": args.event}
            try:
                if not args.target.startswith(("http://", "https://")):
                    raise ValueError("Target URL must start with http:// or https://")
                response = requests.post(args.target, json=payload, timeout=10)
                response.raise_for_status()
                logger.info(
                    f"Successfully triggered '{args.event}' event. Server response: {response.json()}"
                )
                cli_audit_logger.log_event(
                    "command_executed",
                    command="trigger",
                    args=scrub_secrets(vars(args)),
                    status="success",
                )
            except requests.RequestException as e:
                logger.critical(
                    f"CRITICAL: Failed to trigger event on '{args.target}': {e}",
                    exc_info=True,
                )
                cli_audit_logger.log_event(
                    "command_executed",
                    command="trigger",
                    args=scrub_secrets(vars(args)),
                    status="failure",
                    error=str(e),
                )
                alert_operator(
                    f"CRITICAL: Failed to trigger event on '{args.target}': {e}. Aborting.",
                    level="CRITICAL",
                )
                sys.exit(1)
            except ValueError as e:
                logger.critical(
                    f"CRITICAL: Invalid target URL for trigger: {e}", exc_info=True
                )
                cli_audit_logger.log_event(
                    "command_executed",
                    command="trigger",
                    args=scrub_secrets(vars(args)),
                    status="failure",
                    error=str(e),
                )
                alert_operator(
                    f"CRITICAL: Invalid target URL for trigger: {e}. Aborting.",
                    level="CRITICAL",
                )
                sys.exit(1)
        elif args.command == "selftest":
            logger.info("Running self-test...")
            cli_audit_logger.log_event("selftest_start")
            test_passed = True
            try:
                load_analyzer()
                with tempfile.TemporaryDirectory() as tmpdir:
                    dummy_file_path = os.path.join(tmpdir, "dummy_module.py")
                    with open(dummy_file_path, "w") as f:
                        f.write("import os\ndef func(): pass\n")
                    test_analyzer = ImportGraphAnalyzer(
                        tmpdir, config=global_config.get("analyzer", {})
                    )
                    _ = test_analyzer.build_graph()
                    logger.info(
                        "Analyzer module loaded and basic graph analysis performed successfully."
                    )
                    # External tool checks (simulated)

                    for tool in ["ruff", "flake8", "mypy", "bandit", "pytest"]:
                        if shutil.which(tool) is None:
                            logger.error(f"Required tool '{tool}' is missing.")
                            test_passed = False
            except SystemExit:
                logger.error(
                    "Analyzer self-test failed critically and attempted to exit."
                )
                test_passed = False
            except Exception as e:
                logger.error(f"Analyzer self-test failed: {e}", exc_info=True)
                alert_operator(
                    f"CRITICAL: Analyzer self-test failed: {e}.", level="CRITICAL"
                )
                test_passed = False
            try:
                load_fixer()
                logger.info("Fixer module loaded successfully.")
            except SystemExit:
                logger.error("Fixer self-test failed critically and attempted to exit.")
                test_passed = False
            except Exception as e:
                logger.error(f"Fixer self-test failed: {e}", exc_info=True)
                alert_operator(
                    f"CRITICAL: Fixer self-test failed: {e}.", level="CRITICAL"
                )
                test_passed = False
            if not plugin_manager.plugins:
                logger.warning(
                    "No plugins were loaded. Check 'plugins' directory or --plugins-dir."
                )
            else:
                logger.info(f"Plugins loaded: {list(plugin_manager.plugins.keys())}")
            try:
                cli_audit_logger.log_event("selftest_audit_write", status="success")
                logger.info("Audit logger is functional.")
            except Exception as e:
                logger.error(f"Audit logger self-test failed: {e}", exc_info=True)
                alert_operator(
                    f"CRITICAL: Audit logger self-test failed: {e}.", level="CRITICAL"
                )
                test_passed = False
            if test_passed:
                logger.info("Self-test passed.")
                cli_audit_logger.log_event("selftest_complete", status="passed")
                sys.exit(0)
            else:
                logger.error("Self-test FAILED. Check logs for details.")
                cli_audit_logger.log_event("selftest_complete", status="failed")
                sys.exit(1)
        plugin_manager.run_hook(
            "post_command_execution", args=args, config=global_config
        )
    except SystemExit:
        cli_audit_logger.log_event(
            "cli_execution_aborted", reason="system_exit_from_submodule"
        )
        pass
    except Exception as e:
        logger.critical(
            f"CRITICAL: An unexpected error occurred during CLI execution: {e}",
            exc_info=True,
        )
        cli_audit_logger.log_event(
            "cli_execution_failure",
            error=str(e),
            traceback=traceback.format_exc(),
            cli_args=scrub_secrets(vars(args)),
        )
        alert_operator(
            f"CRITICAL: Unexpected error during CLI execution: {e}. Aborting.",
            level="CRITICAL",
            details={"traceback": traceback.format_exc()},
        )
        sys.exit(1)


def main():
    setup_logging(verbose=False, log_format="text")
    try:
        asyncio.run(main_async())
    except (SystemExit, KeyboardInterrupt):
        pass
    except Exception as e:
        logger.critical(
            f"CRITICAL: An unhandled exception occurred in the async main loop: {e}",
            exc_info=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
