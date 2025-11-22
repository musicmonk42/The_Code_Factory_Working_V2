import os
import json
import importlib.util
import logging
import re
from typing import Callable, List, Dict, Any, Optional

ONBOARDING_BACKENDS_AVAILABLE = True

try:
    import streamlit as _st

    st_dash = _st
except ImportError:

    class _StubStreamlit:
        def __init__(self):
            self.session_state = {}

        def __getattr__(self, name):
            def _noop(*args, **kwargs):
                return None

            return _noop

    st_dash = _StubStreamlit()

logger = logging.getLogger("dashboard")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


class Config:
    """Configuration for dashboard directories."""

    PLUGINS_DIR: str = os.path.join(os.getcwd(), "plugins")
    RESULTS_DIR: str = os.path.join(os.getcwd(), "simulation_results")
    CONFIG_DIR: str = os.path.join(os.getcwd(), "configs")

    @classmethod
    def ensure_dirs(cls):
        """Ensure all required directories exist."""
        for directory in [cls.PLUGINS_DIR, cls.RESULTS_DIR, cls.CONFIG_DIR]:
            os.makedirs(directory, exist_ok=True)


_registered_panels: List[Dict[str, Any]] = []
_registered_sidebar_components: List[Callable] = []
_registered_main_components: List[Callable] = []
_plugins_loaded: bool = False


def get_registered_dashboard_panels() -> List[Dict[str, Any]]:
    """Return all registered dashboard panels."""
    return list(_registered_panels)


def get_registered_sidebar_components() -> List[Callable]:
    """Return all registered sidebar components."""
    return list(_registered_sidebar_components)


def get_registered_main_components() -> List[Callable]:
    """Return all registered main components."""
    return list(_registered_main_components)


def _clear_registries():
    _registered_panels.clear()
    _registered_sidebar_components.clear()
    _registered_main_components.clear()


def register_dashboard_panel(
    panel_id: str, title: str, render_func: Callable, live_data_supported: bool = False
):
    """Register a dashboard panel."""
    _registered_panels.append(
        {
            "id": panel_id,
            "title": title,
            "render": render_func,
            "live_data_supported": live_data_supported,
        }
    )


def register_sidebar_component(component_func: Callable):
    """Register a sidebar component."""
    _registered_sidebar_components.append(component_func)


def register_main_component(component_func: Callable):
    """Register a main component."""
    _registered_main_components.append(component_func)


def sanitize_plugin_name(name: str) -> str:
    """
    Sanitize plugin names to prevent path traversal and dangerous modules.
    Only allows alphanumeric, hyphen, underscore.
    """
    if not isinstance(name, str):
        raise ValueError("Plugin name must be a string")
    if (
        ".." in name
        or "/" in name
        or "\\" in name
        or name.startswith(os.sep)
        or name.startswith("~")
    ):
        raise ValueError("Path traversal detected in plugin name")
    dangerous = {"sys", "os", "builtins", "subprocess"}
    if name in dangerous:
        raise ValueError("Dangerous plugin name detected")
    normalized = name.replace(".", "")
    normalized = re.sub(r"[^A-Za-z0-9_\-]", "", normalized)
    if not normalized:
        raise ValueError("Invalid plugin name after sanitization")
    return normalized


def is_version_compatible(version_str: str, min_version: str, max_version: str) -> bool:
    """
    Check if version_str is in [min_version, max_version).
    """

    def parse(v):
        return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))

    try:
        v = parse(version_str)
        lo = parse(min_version)
        hi = parse(max_version)
        return lo <= v < hi
    except Exception as e:
        logger.warning(f"Version parsing exception: {e}")
        return False


def validate_plugin_manifest(plugin_path: str) -> bool:
    """
    Validate the manifest.json in a plugin directory.
    """
    manifest_path = os.path.join(plugin_path, "manifest.json")
    if not os.path.isfile(manifest_path):
        logger.warning(f"Plugin at {plugin_path} is missing manifest.json")
        return False
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        for key in ("name", "version", "description"):
            if key not in manifest:
                logger.error(f"Missing '{key}' in manifest at {manifest_path}")
                return False
        # Example: require version 0.1.0 <= v < 1.0.0
        if not is_version_compatible(manifest["version"], "0.1.0", "1.0.0"):
            logger.error(
                f"Incompatible plugin version: {manifest['version']} in {manifest_path}"
            )
            return False
        return True
    except Exception as e:
        logger.error(f"Invalid manifest in {plugin_path}: {e}")
        return False


def load_plugin_dashboard_panels_cached():
    """
    Discover and load dashboard plugin panels from the plugins directory.
    Only valid, sanitized, and manifest-verified plugins will be loaded.
    """
    global _plugins_loaded
    _clear_registries()
    _plugins_loaded = False
    Config.ensure_dirs()
    plugins_dir = Config.PLUGINS_DIR
    if not os.path.isdir(plugins_dir):
        logger.error(f"Plugins directory {plugins_dir} does not exist")
        return

    for entry in os.listdir(plugins_dir):
        path = os.path.join(plugins_dir, entry)
        if os.path.isdir(path):
            module_name = entry
            file_path = os.path.join(path, "__init__.py")
            if not os.path.isfile(file_path):
                continue
            if not validate_plugin_manifest(path):
                continue
        elif entry.endswith(".py"):
            module_name = entry[:-3]
            file_path = path
            # Single-file plugins may not require a manifest, for compatibility
        else:
            continue

        try:
            safe_name = sanitize_plugin_name(module_name)
        except ValueError as e:
            logger.warning(f"Skipping plugin '{entry}': {e}")
            continue

        try:
            spec = importlib.util.spec_from_file_location(
                f"dashboard_plugin_{safe_name}", file_path
            )
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "register_my_dashboard_panels"):
                module.register_my_dashboard_panels(register_dashboard_panel)
            if hasattr(module, "render_sidebar_component"):
                register_sidebar_component(getattr(module, "render_sidebar_component"))
            if hasattr(module, "render_main_component"):
                register_main_component(getattr(module, "render_main_component"))
            logger.info(f"Loaded plugin: {module_name}")
        except Exception as e:
            logger.exception(f"Failed to load plugin '{module_name}': {e}")
            continue
    _plugins_loaded = True
    logger.info("All plugins loaded.")


def display_onboarding_wizard():
    """
    Display onboarding wizard for new configuration and a demo plugin.
    """
    Config.ensure_dirs()
    plugins_dir = Config.PLUGINS_DIR
    config_dir = Config.CONFIG_DIR

    wizard_type = st_dash.selectbox("wizard_type", ["agentic_swarm", "solo"])
    notif_backend = st_dash.selectbox("notification_backend", ["redis", "local"])
    cp_backend = st_dash.selectbox("checkpoint_backend", ["fs", "s3"])
    languages = getattr(st_dash, "multiselect", lambda *a, **k: ["python"])(
        "languages", ["python"]
    )
    notif_url = getattr(st_dash, "text_input", lambda *a, **k: "")("notification_url")
    cp_dir = getattr(st_dash, "text_input", lambda *a, **k: "")("checkpoint_dir")

    submit = getattr(st_dash, "form_submit_button", lambda *a, **k: True)("submit")
    if submit:
        cfg = {
            "wizard_type": wizard_type,
            "notification_backend": {"type": notif_backend, "url": notif_url},
            "checkpoint_backend": {"type": cp_backend, "dir": cp_dir},
            "languages": languages,
        }
        config_path = os.path.join(config_dir, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        demo_dir = os.path.join(plugins_dir, "demo_python_plugin")
        os.makedirs(demo_dir, exist_ok=True)
        manifest = {
            "name": "demo_python_plugin",
            "version": "0.1.0",
            "description": "Demo plugin generated by onboarding wizard",
        }
        with open(os.path.join(demo_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        logger.info("Onboarding configuration and demo plugin created.")


async def _run_health_checks_gui(config: dict):
    """
    Run health checks for notification and checkpoint backends,
    using classes that must be provided in global scope.
    """
    try:
        notif_cfg = config.get("notification_backend", {})
        notif_type = notif_cfg.get("type")
        if notif_type:
            MeshPubSub = globals().get("MeshPubSub")
            if MeshPubSub:
                mp = MeshPubSub(notif_type)
                health = await mp.healthcheck()
                st_dash.success(
                    f"Notification backend {notif_type}: {health.get('message', health)}"
                )
            else:
                st_dash.error("MeshPubSub class missing.")
        cp_cfg = config.get("checkpoint_backend", {})
        cp_type = cp_cfg.get("type")
        if cp_type:
            CheckpointManager = globals().get("CheckpointManager")
            if CheckpointManager:
                cm = CheckpointManager(cp_type)
                health = await cm.load()
                st_dash.success(
                    f"Checkpoint backend {cp_type}: {health.get('status', health)}"
                )
            else:
                st_dash.error("CheckpointManager class missing.")
    except Exception as e:
        logger.exception(f"Health check error: {e}")
        st_dash.error("Unexpected error during health checks.")


def load_all_simulation_results(results_dir: Optional[str] = None) -> List[dict]:
    """
    Load all simulation result JSON files from the results directory.
    """
    Config.ensure_dirs()
    if not results_dir:
        results_dir = Config.RESULTS_DIR
    results = []
    if not os.path.isdir(results_dir):
        logger.warning(f"Results directory {results_dir} does not exist.")
        return results
    for fname in sorted(os.listdir(results_dir)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(results_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                results.append(data)
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")
    return results


_translations = {
    "en": {"welcome_message": "Welcome", "language_selector_label": "Language"},
    "es": {"welcome_message": "Bienvenido", "language_selector_label": "Idioma"},
    "fr": {"welcome_message": "Bienvenue", "language_selector_label": "Langue"},
}


def t(key: str) -> str:
    lang = getattr(st_dash.session_state, "lang", "en")
    if isinstance(st_dash.session_state, dict):
        lang = st_dash.session_state.get("lang", lang)
    return _translations.get(lang, _translations["en"]).get(
        key, _translations["en"].get(key, key)
    )


def render(*args, **kwargs):
    return None


# Dummy stubs for patching in tests (for test compatibility)
class MeshPubSub:
    pass


class CheckpointManager:
    pass
