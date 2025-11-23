"""
Plugin Manager for the self-healing import fixer.

This module provides a robust and secure framework for managing and loading external plugins.
It supports dynamic registration of various hooks (healers, validators, diff viewers) while
enforcing strict security measures like source verification via HMAC signatures and
whitelisted directories, especially in a production environment. The design prioritizes
lazy loading, clear error handling, and comprehensive audit logging to ensure system
stability and security.
"""

import os
import sys
import logging
import importlib.util
import hmac
import hashlib
import asyncio
import shutil
from collections import defaultdict
from typing import List, Dict, Set, Optional, Callable, Any, Mapping, TYPE_CHECKING
from abc import ABC, abstractmethod
from pathlib import Path
import re
from types import MappingProxyType
import stat

# Ensure plugins importing "fixer_plugins" refer to this module instance too
sys.modules.setdefault("fixer_plugins", sys.modules[__name__])

logger = logging.getLogger(__name__)

# --- Required Core Dependencies ---
try:
    from .compat_core import (
        alert_operator,
        scrub_secrets,
        audit_logger,
        SECRETS_MANAGER,
    )
except ImportError as e:
    logger.critical(f"Missing core dependency for fixer_plugins: {e}")
    raise RuntimeError(f"[CRITICAL][PLUGINS] Missing core dependency: {e}")

# --- Optional caching dependency ---
if TYPE_CHECKING:
    pass  # for type checkers only


def _get_plugin_cache():
    from .cache_layer import get_cache  # runtime import avoids circulars

    return get_cache()


# --- Custom Exceptions ---
class AnalyzerCriticalError(RuntimeError):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    """

    def __init__(self, message: str, alert_level: str = "CRITICAL"):
        super().__init__(f"[CRITICAL][PLUGINS] {message}")
        try:
            alert_operator(message, level=alert_level)
        except Exception:
            pass


class NonCriticalError(Exception):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """

    pass


class PluginLoadError(NonCriticalError):
    """Exception raised when a plugin fails to load."""

    pass


class PluginValidationError(AnalyzerCriticalError):
    """Exception raised when a plugin fails security or validation checks."""

    pass


def _callable_module_name(fn: Callable) -> Optional[str]:
    """
    Safely retrieves the module name for a given callable.
    Handles functions, bound methods, and objects with __call__.
    """
    mod = getattr(fn, "__module__", None)
    if mod:
        return mod
    f = getattr(fn, "__func__", None)
    if f:
        return getattr(f, "__module__", None)
    call = getattr(fn, "__call__", None)
    if call:
        return getattr(call, "__module__", None)
    return None


# --- Plugin Source Verification: HMAC Key Management ---
PLUGIN_SIGNATURE_KEY_ENV = "FIXER_PLUGIN_SIGNATURE_KEY"
_plugin_signature_key: Optional[bytes] = None
_plugin_key_source: Optional[str] = None


def _get_plugin_signature_key(production_mode: bool) -> bytes:
    """
    Retrieves or generates the HMAC key for plugin signature verification.
    """
    global _plugin_signature_key, _plugin_key_source
    if _plugin_signature_key is None:
        key_str = SECRETS_MANAGER.get_secret(PLUGIN_SIGNATURE_KEY_ENV, required=production_mode)
        if key_str:
            _plugin_signature_key = key_str.encode("utf-8")
            _plugin_key_source = "secret"
        else:
            if production_mode:
                raise AnalyzerCriticalError("Plugin signature key not found for production.")
            _plugin_signature_key = os.urandom(32)
            _plugin_key_source = "random"
            logger.warning(
                "FIXER_PLUGIN_SIGNATURE_KEY_ENV not set. Generated a random key for plugin signing. THIS IS INSECURE FOR PRODUCTION."
            )
            alert_operator(
                "WARNING: Plugin signature key not set. Using insecure random key. IMMEDIATE ACTION REQUIRED.",
                level="HIGH",
            )
    else:
        if production_mode and _plugin_key_source != "secret":
            raise AnalyzerCriticalError(
                "Random HMAC key was initialized in a non-production session; refusing to use in production."
            )
    return _plugin_signature_key


async def _verify_plugin_signature_async(
    file_path: Path, expected_signature: str, production_mode: bool
) -> bool:
    """
    Verifies the HMAC signature of a plugin file using a timing-safe comparison, with caching.
    """
    try:
        if not os.access(file_path, os.R_OK):
            raise AnalyzerCriticalError(f"No read access to plugin {file_path}")

        file_stat = file_path.stat()
        cache_key = f"plugin_signature:{file_path}:{file_stat.st_mtime_ns}:{file_stat.st_size}"

        cache = await _get_plugin_cache()
        cached_signature = await cache.get(cache_key)

        if cached_signature and hmac.compare_digest(cached_signature, expected_signature):
            logger.debug(f"Plugin signature verified from cache for {file_path}.")
            return True

        with open(file_path, "rb") as f:
            file_content = f.read()

        h = hmac.new(_get_plugin_signature_key(production_mode), file_content, hashlib.sha256)
        calculated_signature = h.hexdigest()

        if hmac.compare_digest(calculated_signature, expected_signature):
            logger.debug(f"Plugin signature verified for {file_path}.")
            await cache.setex(cache_key, 86400, calculated_signature)
            return True
        else:
            logger.error(f"Plugin signature MISMATCH for {file_path}. Possible tampering detected!")
            audit_logger.log_event(
                "plugin_signature_mismatch",
                file=str(file_path),
                calculated=calculated_signature,
                expected=expected_signature,
            )
            return False
    except FileNotFoundError:
        logger.error(f"Plugin file not found for signature verification: {file_path}")
        return False
    except AnalyzerCriticalError as e:
        raise e
    except Exception as e:
        logger.error(
            f"Error during plugin signature verification for {file_path}: {e}",
            exc_info=True,
        )
        alert_operator(
            f"ERROR: Plugin signature verification failed for {file_path}: {e}",
            level="ERROR",
        )
        return False


# --- Plugin Interface ---
class Plugin(ABC):
    """
    Abstract base class for all plugins. All plugins must inherit from this class.
    """

    name: str

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def register(self, manager: "PluginManager") -> None:
        """
        Registers the plugin's components (healers, validators, hooks) with the PluginManager.

        Args:
            manager (PluginManager): The manager instance to register with.
        """
        pass


# --- Plugin Manager ---
class PluginManager:
    """
    A manager for extensibility hooks and plugins within the self-healing import fixer.
    Allows dynamic loading and registration of healers, validators, and diff viewers.
    """

    _SAFE_MOD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

    def __init__(self, config: Dict[str, Any] = None):
        """
        Initializes the PluginManager with a configuration.
        """
        self.config = config or {}
        self.production_mode = bool(
            self.config.get(
                "production_mode",
                os.getenv("PRODUCTION_MODE", "false").lower() == "true",
            )
        )

        self.hooks: Dict[str, List[Callable]] = defaultdict(list)
        self.healers: List[Callable] = []
        self.validators: List[Callable] = []
        self.diff_viewers: List[Callable] = []

        whitelisted_dirs = [
            Path(d).resolve() for d in self.config.get("whitelisted_plugin_dirs", [])
        ]
        approved_plugins = self.config.get("approved_plugins", {})

        self.whitelisted_plugin_dirs = tuple(whitelisted_dirs)
        self.approved_plugins = MappingProxyType(dict(approved_plugins))

        if self.production_mode and not self.whitelisted_plugin_dirs:
            raise AnalyzerCriticalError(
                "In PRODUCTION_MODE, 'whitelisted_plugin_dirs' must be configured for PluginManager. Aborting startup."
            )

        if self.production_mode:
            self._validate_approved_plugins(self.approved_plugins)

        self._loaded_modules: Set[str] = set()
        self._in_plugin_registration: bool = False
        self._load_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        logger.info("PluginManager initialized.")
        audit_logger.log_event(
            "plugin_manager_init",
            whitelisted_dirs=[str(d) for d in self.whitelisted_plugin_dirs],
            approved_plugins_count=len(self.approved_plugins),
            production_mode=self.production_mode,
            hmac_key_source=_plugin_key_source,
        )

    def _validate_approved_plugins(self, approved_plugins: Mapping[str, str]) -> None:
        for module_name, signature in approved_plugins.items():
            if not self._SAFE_MOD_RE.match(module_name):
                raise PluginValidationError(
                    f"Invalid plugin name in approved_plugins list: {module_name!r}"
                )
            if not self._HEX_DIGEST_RE.match(signature):
                raise PluginValidationError(
                    f"Invalid signature for plugin {module_name!r}. Must be a 64-char lowercase hex SHA-256 digest."
                )

    def _begin_plugin_registration(self) -> None:
        self._in_plugin_registration = True

    def _end_plugin_registration(self) -> None:
        self._in_plugin_registration = False

    def register_hook(self, hook_name: str, func: Callable) -> None:
        if self.production_mode and not self._in_plugin_registration:
            raise PluginValidationError(
                f"Runtime hook registration for '{hook_name}' is forbidden in production."
            )
        if func not in self.hooks[hook_name]:
            self.hooks[hook_name].append(func)
            logger.debug(f"Registered hook '{hook_name}': {func.__name__}")
            audit_logger.log_event(
                "plugin_registered",
                type="hook",
                hook_name=hook_name,
                func_name=func.__name__,
                module=_callable_module_name(func),
            )

    def register_healer(self, healer: Callable) -> None:
        if self.production_mode and not self._in_plugin_registration:
            raise PluginValidationError(
                f"Runtime healer registration for '{healer.__name__}' is forbidden in production."
            )
        if healer not in self.healers:
            self.healers.append(healer)
            logger.debug(f"Registered healer: {healer.__name__}")
            audit_logger.log_event(
                "plugin_registered",
                type="healer",
                func_name=healer.__name__,
                module=_callable_module_name(healer),
            )

    def register_validator(self, validator: Callable) -> None:
        if self.production_mode and not self._in_plugin_registration:
            raise PluginValidationError(
                f"Runtime validator registration for '{validator.__name__}' is forbidden in production."
            )
        if validator not in self.validators:
            self.validators.append(validator)
            logger.debug(f"Registered validator: {validator.__name__}")
            audit_logger.log_event(
                "plugin_registered",
                type="validator",
                func_name=validator.__name__,
                module=_callable_module_name(validator),
            )

    def register_diff_viewer(self, viewer: Callable) -> None:
        if self.production_mode and not self._in_plugin_registration:
            raise PluginValidationError(
                f"Runtime diff viewer registration for '{viewer.__name__}' is forbidden in production."
            )
        if viewer not in self.diff_viewers:
            self.diff_viewers.append(viewer)
            logger.debug(f"Registered diff viewer: {viewer.__name__}")
            audit_logger.log_event(
                "plugin_registered",
                type="diff_viewer",
                func_name=viewer.__name__,
                module=_callable_module_name(viewer),
            )

    def run_hook(self, hook_name: str, *args, **kwargs) -> None:
        logger.debug(f"Running hook: {hook_name}")
        args_safe = scrub_secrets(str(args))
        kwargs_safe = scrub_secrets(str(kwargs))
        audit_logger.log_event(
            "plugin_hook_run",
            hook_name=hook_name,
            args_summary=args_safe[:100],
            kwargs_summary=kwargs_safe[:100],
        )

        stop_on_error = bool(self.config.get("stop_on_hook_error", True))
        errors = []
        for func in self.hooks.get(hook_name, []):
            try:
                func(*args, **kwargs)
            except Exception as e:
                logger.exception("Hook '%s' function '%s' failed.", hook_name, func.__name__)
                audit_logger.log_event(
                    "plugin_hook_failure",
                    hook_name=hook_name,
                    func_name=func.__name__,
                    module=_callable_module_name(func),
                    error=str(e),
                )
                if stop_on_error:
                    raise AnalyzerCriticalError(
                        f"Plugin hook '{hook_name}' failed in '{func.__name__}'. Aborting due to 'stop_on_hook_error' config.",
                        alert_level="CRITICAL",
                    )
                errors.append((func.__name__, str(e)))

        if errors:
            alert_operator(
                f"{len(errors)} hook(s) failed in '{hook_name}'. See logs for details.",
                level="ERROR",
            )

    async def load_plugin(self, module_name: str) -> None:
        lock = self._load_locks[module_name]
        async with lock:
            if module_name in self._loaded_modules:
                return

            if not self._SAFE_MOD_RE.match(module_name):
                raise PluginValidationError(f"Invalid plugin module name: {module_name!r}")

            plugin_found = False
            for plugin_dir in self.whitelisted_plugin_dirs:
                candidate = (plugin_dir / f"{module_name}.py").resolve()
                base = plugin_dir.resolve()

                try:
                    if not candidate.is_relative_to(base):
                        continue
                except AttributeError:
                    try:
                        candidate.relative_to(base)
                    except ValueError:
                        continue

                if candidate.exists():
                    try:
                        await self._load_plugin_file_async(module_name, candidate)
                        plugin_found = True
                        break
                    except (AnalyzerCriticalError, PluginValidationError):
                        raise
                    except Exception as e:
                        raise PluginLoadError(f"Failed to load plugin '{module_name}': {e}") from e

            if not plugin_found:
                raise NonCriticalError(
                    f"Plugin '{module_name}' not found in any whitelisted directory."
                )

    def unload_plugin(self, module_name: str) -> None:
        if module_name not in self._loaded_modules:
            logger.warning("Attempted to unload non-loaded plugin: %s", module_name)
            return

        self._loaded_modules.remove(module_name)
        sys.modules.pop(module_name, None)

        def _filter(seq):
            return [f for f in seq if _callable_module_name(f) != module_name]

        removed = 0
        for hk, hook_list in self.hooks.items():
            before = len(hook_list)
            self.hooks[hk] = _filter(hook_list)
            removed += before - len(self.hooks[hk])

        before = len(self.healers)
        self.healers = _filter(self.healers)
        removed += before - len(self.healers)

        before = len(self.validators)
        self.validators = _filter(self.validators)
        removed += before - len(self.validators)

        before = len(self.diff_viewers)
        self.diff_viewers = _filter(self.diff_viewers)
        removed += before - len(self.diff_viewers)

        logger.info("Unloaded plugin %s and unregistered %d components.", module_name, removed)
        audit_logger.log_event(
            "plugin_unloaded", module_name=module_name, components_unregistered=removed
        )

    async def _load_plugin_file_async(self, module_name: str, full_plugin_path: Path) -> None:
        if module_name in self._loaded_modules:
            return

        audit_logger.log_event(
            "plugin_load_attempt",
            module_name=module_name,
            path=scrub_secrets(str(full_plugin_path)),
        )

        max_size = int(self.config.get("max_plugin_bytes", 0))
        if max_size and full_plugin_path.stat().st_size > max_size:
            raise PluginValidationError(
                f"Plugin '{module_name}' exceeds size limit of {max_size} bytes."
            )

        if self.production_mode and self.config.get("enforce_posix_perms", False):
            try:
                st = os.stat(full_plugin_path)
                if st.st_mode & stat.S_IWOTH:
                    raise PluginValidationError(
                        f"Plugin '{module_name}' at {full_plugin_path} is world-writable."
                    )
                if hasattr(os, "getuid") and st.st_uid not in {os.getuid(), 0}:
                    raise PluginValidationError(
                        f"Plugin '{module_name}' at {full_plugin_path} not owned by current user/root."
                    )
            except OSError as e:
                raise AnalyzerCriticalError(
                    f"Failed to stat plugin file {full_plugin_path}: {e}",
                    level="CRITICAL",
                )

        expected_signature = self.approved_plugins.get(module_name)
        if self.production_mode and not expected_signature:
            raise PluginValidationError(
                f"Plugin '{module_name}' is not in approved_plugins or has no signature."
            )
        if (
            self.production_mode
            and expected_signature
            and not self._HEX_DIGEST_RE.match(expected_signature)
        ):
            raise PluginValidationError(
                f"Plugin signature for '{module_name}' must be a 64-char lowercase hex SHA-256 digest."
            )

        if self.production_mode and expected_signature:
            if not await _verify_plugin_signature_async(
                full_plugin_path, expected_signature, self.production_mode
            ):
                raise PluginValidationError(
                    f"Plugin '{module_name}' signature verification failed."
                )

        spec = importlib.util.spec_from_file_location(module_name, full_plugin_path)
        if spec is None or spec.loader is None:
            raise PluginLoadError(
                f"Could not get module spec or loader for plugin: {full_plugin_path}"
            )

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module

        hooks_snapshot = {k: len(v) for k, v in self.hooks.items()}
        healers_len = len(self.healers)
        validators_len = len(self.validators)
        viewers_len = len(self.diff_viewers)

        try:
            if self.production_mode and expected_signature:
                # Re-verify in a lock-step to prevent race conditions on the file.
                if not await _verify_plugin_signature_async(
                    full_plugin_path, expected_signature, self.production_mode
                ):
                    raise PluginValidationError(
                        f"Plugin '{module_name}' changed on disk during load."
                    )

            spec.loader.exec_module(module)

            plugins = []
            for name in dir(module):
                obj = getattr(module, name)
                if isinstance(obj, type) and issubclass(obj, Plugin) and obj is not Plugin:
                    plugins.append(obj)

            if not plugins:
                raise PluginLoadError(f"Plugin '{module_name}' has no Plugin subclass.")

            self._begin_plugin_registration()
            try:
                for cls in plugins:
                    cls(name=cls.__name__).register(self)
            finally:
                self._end_plugin_registration()

            self._loaded_modules.add(module_name)
            logger.info("Loaded plugin: %s from %s", module_name, full_plugin_path)
            audit_logger.log_event(
                "plugin_loaded", module_name=module_name, path=str(full_plugin_path)
            )
        except Exception:
            self.healers[healers_len:] = []
            self.validators[validators_len:] = []
            self.diff_viewers[viewers_len:] = []
            for hk, before in hooks_snapshot.items():
                if hk in self.hooks:
                    self.hooks[hk][before:] = []
            for hk in list(self.hooks.keys()):
                if hk not in hooks_snapshot and not self.hooks[hk]:
                    del self.hooks[hk]
            sys.modules.pop(module_name, None)
            raise


def make_plugin_manager(config: Dict[str, Any]) -> PluginManager:
    """Factory function to create a PluginManager instance."""
    return PluginManager(config=config)


# Helper function for testing
def _reset_plugin_key_for_tests() -> None:
    global _plugin_signature_key, _plugin_key_source
    _plugin_signature_key = None
    _plugin_key_source = None


# Example usage (for testing this module independently)
if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    logging.getLogger(__name__).setLevel(logging.DEBUG)

    # --- Setup Test Environment ---
    test_base_dir = Path("test_plugin_env")
    test_plugin_dir = test_base_dir / "plugins"

    if test_base_dir.exists():
        shutil.rmtree(test_base_dir)

    test_plugin_dir.mkdir(parents=True, exist_ok=True)

    # --- Create dummy plugin content (as a Plugin class) ---
    healer_plugin_content = """
from fixer_plugins import Plugin, PluginManager
import logging
logger = logging.getLogger(__name__)

class MyHealerPlugin(Plugin):
    def register(self, manager: PluginManager) -> None:
        logger.info(f"MyHealerPlugin: Registering with manager.")
        manager.register_healer(self.my_custom_healer)
        manager.register_hook("pre_healing", self.my_pre_hook_function)

    def my_custom_healer(self, file_path: str, problem_details: dict) -> str:
        logger.info(f"MyCustomHealer: Healing {file_path} for {problem_details.get('type')}")
        return f"Fixed code for {file_path}"

    def my_pre_hook_function(self, context: str, sensitive_info: str) -> None:
        logger.info(f"MyPreHook: Running before healing with context: {context}. Sensitive info: {sensitive_info}")

"""
    validator_plugin_content = """
from fixer_plugins import Plugin, PluginManager
import logging
logger = logging.getLogger(__name__)

class MyValidatorPlugin(Plugin):
    def register(self, manager: PluginManager) -> None:
        logger.info(f"MyValidatorPlugin: Registering with manager.")
        manager.register_validator(self.my_custom_validator)

    def my_custom_validator(self, file_path: str) -> bool:
        logger.info(f"MyCustomValidator: Validating {file_path}")
        if "bad_code" in file_path:
            logger.warning(f"MyCustomValidator: Detected 'bad_code' in {file_path}")
            return False
        return True
"""
    # Create plugin files
    healer_plugin_path = test_plugin_dir / "plugin_my_healer_plugin.py"
    validator_plugin_path = test_plugin_dir / "plugin_my_validator_plugin.py"

    with open(healer_plugin_path, "w") as f:
        f.write(healer_plugin_content)
    with open(validator_plugin_path, "w") as f:
        f.write(validator_plugin_content)

    async def main_test():
        _reset_plugin_key_for_tests()
        healer_signature = hmac.new(
            _get_plugin_signature_key(production_mode=False),
            healer_plugin_content.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        validator_signature = hmac.new(
            _get_plugin_signature_key(production_mode=False),
            validator_plugin_content.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        plugin_manager = make_plugin_manager(
            config={
                "whitelisted_plugin_dirs": [str(test_plugin_dir)],
                "approved_plugins": {
                    "plugin_my_healer_plugin": healer_signature,
                    "plugin_my_validator_plugin": validator_signature,
                },
            }
        )

        print("\n--- Testing Lazy Loading of Plugins ---")
        await plugin_manager.load_plugin("plugin_my_healer_plugin")
        await plugin_manager.load_plugin("plugin_my_validator_plugin")

        print("\n--- Running Hooks ---")
        plugin_manager.run_hook(
            "pre_healing",
            context="initial scan context",
            sensitive_info="my_secret_data",
        )

        print("\n--- Running Healers ---")
        if plugin_manager.healers:
            healer_result = plugin_manager.healers[0]("test_file.py", {"type": "syntax_error"})
            print(f"Healer Result: {healer_result}")
        else:
            print("No healers registered.")

        print("\n--- Running Validators ---")
        if plugin_manager.validators:
            validation_ok = plugin_manager.validators[0]("test_file_ok.py")
            print(f"Validation Result (OK): {validation_ok}")
            validation_bad = plugin_manager.validators[0]("bad_code_file.py")
            print(f"Validation Result (Bad): {validation_bad}")
        else:
            print("No validators registered.")

        # Test: Attempt to load from non-whitelisted directory (should fail)
        print("\n--- Testing Load from Non-Whitelisted Directory (expecting error) ---")
        unwhitelisted_dir = test_base_dir / "unwhitelisted_plugins"
        unwhitelisted_dir.mkdir(exist_ok=True)
        (unwhitelisted_dir / "plugin_unapproved.py").write_text("print('unapproved')")
        try:
            plugin_manager_unapproved = make_plugin_manager(
                config={"whitelisted_plugin_dirs": [], "approved_plugins": {}}
            )
            await plugin_manager_unapproved.load_plugin("plugin_unapproved")
        except NonCriticalError as e:
            print(
                f"Caught expected NonCriticalError for loading from non-whitelisted directory: {e}"
            )

        # Test: Attempt to load unapproved/unsignd plugin (should fail)
        print("\n--- Testing Load of Unapproved/Unsigned Plugin (expecting error) ---")
        unapproved_plugin_dir = test_base_dir / "unapproved_plugins_signed"
        unapproved_plugin_dir.mkdir(exist_ok=True)
        unapproved_plugin_path = unapproved_plugin_dir / "plugin_unapproved_signed.py"
        unapproved_plugin_path.write_text(
            "from fixer_plugins import Plugin, PluginManager\nclass TestPlugin(Plugin):\n\tdef register(self, manager: PluginManager):\n\t\tpass\n"
        )
        try:
            _reset_plugin_key_for_tests()
            plugin_manager_unsigned = make_plugin_manager(
                config={
                    "whitelisted_plugin_dirs": [str(unapproved_plugin_dir)],
                    "approved_plugins": {"plugin_unapproved_signed": "a" * 64},
                    "production_mode": True,
                }
            )
            await plugin_manager_unsigned.load_plugin("plugin_unapproved_signed")
        except (PluginValidationError, AnalyzerCriticalError) as e:
            print(f"Caught expected security error: {e}")

        # Test: Tampered plugin (should fail)
        print("\n--- Testing Tampered Plugin (expecting error) ---")
        tampered_plugin_dir = test_base_dir / "tampered_plugins"
        tampered_plugin_dir.mkdir(exist_ok=True)
        tampered_plugin_path = tampered_plugin_dir / "plugin_tampered_healer.py"
        tampered_plugin_path.write_text(healer_plugin_content + "\n# TAMPERED LINE")

        tampered_signature_original = hmac.new(
            _get_plugin_signature_key(production_mode=False),
            healer_plugin_content.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        try:
            _reset_plugin_key_for_tests()
            plugin_manager_tampered = make_plugin_manager(
                config={
                    "whitelisted_plugin_dirs": [str(tampered_plugin_dir)],
                    "approved_plugins": {"plugin_tampered_healer": tampered_signature_original},
                    "production_mode": True,
                }
            )
            await plugin_manager_tampered.load_plugin("plugin_tampered_healer")
        except (PluginValidationError, AnalyzerCriticalError) as e:
            print(f"Caught expected security error: {e}")

        # Test: Path Traversal (should fail)
        print("\n--- Testing Path Traversal (expecting error) ---")
        try:
            await plugin_manager.load_plugin("../plugin_my_healer_plugin")
        except PluginValidationError as e:
            print(f"Caught expected PluginValidationError for path traversal: {e}")
        try:
            await plugin_manager.load_plugin("plugins/plugin_my_healer_plugin")
        except PluginValidationError as e:
            print(f"Caught expected PluginValidationError for path traversal: {e}")

    print("\n--- Cleaning up test environment ---")
    asyncio.run(main_test())
    if test_base_dir.exists():
        shutil.rmtree(test_base_dir)
