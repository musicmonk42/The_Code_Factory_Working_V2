from __future__ import annotations
import os
import sys
import json
import glob
import logging
import importlib.util
import traceback  # Import traceback at the top-level
import platform  # For platform.python_version() in generated manifests
import re  # For sanitizing plugin names
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
import time
import asyncio  # Import asyncio for running async functions

# --- Configuration & Setup ---

DASHBOARD_CORE_VERSION = "1.2.0"  # Introduce a core version constant

# Ensure logging is always initialized at the very top.
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Apply nest_asyncio for handling nested event loops in Streamlit
try:
    import nest_asyncio

    nest_asyncio.apply()
except ImportError:
    logging.warning("nest_asyncio not installed. Async operations may fail in some environments.")

# Use python-dotenv to optionally load environment variables from a .env file
try:
    from dotenv import load_dotenv

    load_dotenv()  # Load .env file if it exists
    logging.info("Loaded environment variables from .env file (if present).")
except ImportError:
    logging.info("python-dotenv not installed. Skipping .env file loading.")


# Define a simple Config class for shared constants, similar to app.config
class Config:
    PLUGINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
    CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")
    RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "simulation_results")


# Import PluginManager
PLUGIN_MANAGER_AVAILABLE = False
temp_sys_path_added_plugin_manager = False
# Use Config.PLUGINS_DIR for consistency
current_plugins_dir = Config.PLUGINS_DIR
if current_plugins_dir not in sys.path:
    sys.path.insert(0, current_plugins_dir)
    temp_sys_path_added_plugin_manager = True
try:
    from plugin_manager import PluginManager

    PLUGIN_MANAGER_AVAILABLE = True
except ImportError as e:
    logging.warning(
        f"PluginManager is not available. Plugin Gallery functionality will be limited. Error: {e}"
    )
finally:
    # Remove the temporary path addition
    if temp_sys_path_added_plugin_manager:
        sys.path.pop(0)

# Import MeshPubSub and CheckpointManager for onboarding/health checks
ONBOARDING_BACKENDS_AVAILABLE = False
temp_sys_path_added_onboarding = False
current_dir_added = False
parent_dir_added = False

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # Add current dir
    current_dir_added = True
parent_dir_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if parent_dir_path not in sys.path:
    sys.path.insert(0, parent_dir_path)  # Add parent dir
    parent_dir_added = True

try:
    from mesh_adapter import MeshPubSub
    from checkpoint import CheckpointManager

    ONBOARDING_BACKENDS_AVAILABLE = True
except ImportError as e:
    logging.warning(
        f"MeshPubSub or CheckpointManager not found. Onboarding and full health checks will be limited. Error: {e}"
    )
finally:
    # Clean up sys.path additions if they were temporary
    if current_dir_added:
        sys.path.remove(os.path.dirname(os.path.abspath(__file__)))
    if parent_dir_added:
        sys.path.remove(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

STREAMLIT_AVAILABLE = False
try:
    import streamlit as st_dash
    from streamlit_autorefresh import st_autorefresh  # New import for auto-refresh

    STREAMLIT_AVAILABLE = True
except ImportError:
    logging.warning("Streamlit is not installed. Dashboard functionality will be unavailable.")

# Advanced analytics (Plotly for richer charts)
PLOTLY_AVAILABLE = False
try:
    import plotly.express as px
    import plotly.graph_objects as go

    PLOTLY_AVAILABLE = True
except ImportError:
    pass

# Real-time data streaming (Redis Pub/Sub example)
REDIS_AVAILABLE = False
redis_client = None
try:
    import redis

    REDIS_AVAILABLE = True
    REDIS_URL = os.environ.get("SIM_DASHBOARD_REDIS_URL", "redis://localhost:6379/0")
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        # Test connection
        redis_client.ping()
        logging.info("Redis client connected successfully.")
    except redis.exceptions.ConnectionError as e:
        REDIS_AVAILABLE = False
        redis_client = None
        logging.warning(
            f"Could not connect to Redis at {REDIS_URL}: {e}. Live data streaming will be unavailable."
        )
except ImportError:
    pass

# Ensure necessary directories exist
os.makedirs(Config.CONFIG_DIR, exist_ok=True)
os.makedirs(Config.RESULTS_DIR, exist_ok=True)
os.makedirs(Config.PLUGINS_DIR, exist_ok=True)

# Dangerous module names to blacklist for plugin loading
DANGEROUS_NAMES = {
    "os",
    "sys",
    "subprocess",
    "shutil",
    "importlib",
    "socket",
    "threading",
    "multiprocessing",
    "ctypes",
    "builtins",
}

# Global lists for dashboard components registered by plugins
_dashboard_plugin_panels: List[Dict[str, Any]] = []
_dashboard_sidebar_components: List[Dict[str, Any]] = []
_dashboard_main_components: List[Dict[str, Any]] = []


# --- Decorators for Plugin/UI Callbacks ---
def plugin_callback_handler(func: Callable):
    """
    A decorator to catch exceptions in plugin-registered functions (render_function, sidebar/main components).
    Logs the error with user context and plugin name.
    """

    def wrapper(*args, **kwargs):
        plugin_name = "unknown"
        user_context = st_dash.session_state.get("user", "anonymous")
        user_role = st_dash.session_state.get("user_role", "none")

        # Attempt to infer plugin name from arguments if available
        # This is a heuristic and might need refinement based on how plugins pass context
        if args and isinstance(args[0], dict) and "plugin" in args[0]:
            plugin_name = args[0]["plugin"]
        elif "plugin" in kwargs:
            plugin_name = kwargs["plugin"]
        elif "self" in kwargs and hasattr(kwargs["self"], "plugin_name"):  # For class methods
            plugin_name = kwargs["self"].plugin_name
        elif hasattr(func, "__module__"):
            plugin_name = func.__module__.split(".")[0]  # Heuristic for module-based plugins

        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_message = (
                f"Error in plugin '{plugin_name}' (User: {user_context}, Role: {user_role}): {e}"
            )
            logging.error(error_message, exc_info=True)
            if STREAMLIT_AVAILABLE:
                st_dash.error(
                    f"An error occurred in a component from plugin '{plugin_name}'. Please check logs for details. Error: {e}"
                )
            return None

    return wrapper


# --- Plugin Registration Functions ---


def register_dashboard_panel(
    panel_id: str,
    title: str,
    render_function: Callable[[Any, Dict[str, Any]], None],
    description: str = "",
    roles: Optional[List[str]] = None,
    live_data_supported: bool = False,
):
    _dashboard_plugin_panels.append(
        {
            "id": panel_id,
            "title": title,
            "render_function": plugin_callback_handler(render_function),  # Apply decorator
            "description": description,
            "roles": roles if roles is not None else [],
            "live_data_supported": live_data_supported,
        }
    )
    logging.info(f"Registered dashboard panel: {title} (ID: {panel_id})")


def get_registered_dashboard_panels() -> List[Dict[str, Any]]:
    return _dashboard_plugin_panels


def get_registered_sidebar_components() -> List[Dict[str, Any]]:
    return _dashboard_sidebar_components


def get_registered_main_components() -> List[Dict[str, Any]]:
    return _dashboard_main_components


@st_dash.cache_resource(show_spinner="Loading plugins...")  # Cache plugin discovery
def load_plugin_dashboard_panels_cached():
    """
    Loads dashboard panels and UI components from plugins, with caching.
    This function should be called only once during app initialization.
    """
    logging.info("Starting plugin dashboard panel and UI component loading...")
    _dashboard_plugin_panels.clear()
    _dashboard_sidebar_components.clear()
    _dashboard_main_components.clear()

    if not os.path.exists(Config.PLUGINS_DIR):
        logging.warning(
            f"Plugins directory not found: {Config.PLUGINS_DIR}. No dashboard plugins will be loaded."
        )
        return

    original_sys_path = list(sys.path)
    if Config.PLUGINS_DIR not in sys.path:
        sys.path.insert(0, Config.PLUGINS_DIR)

    # Load single-file plugins
    for plugin_file in glob.glob(os.path.join(Config.PLUGINS_DIR, "*.py")):
        module_name = os.path.basename(plugin_file)[:-3]
        if module_name == "__init__" or module_name in DANGEROUS_NAMES:
            logging.warning(
                f"Skipping potentially dangerous or invalid plugin module: {module_name}"
            )
            continue
        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            if spec is None:
                logging.warning(f"Could not get module spec for plugin: {plugin_file}")
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Check for API version compatibility
            min_core_version = getattr(module, "MIN_CORE_VERSION", "0.0.0")
            max_core_version = getattr(module, "MAX_CORE_VERSION", "999.999.999")
            if not is_version_compatible(
                DASHBOARD_CORE_VERSION, min_core_version, max_core_version
            ):  # Use DASHBOARD_CORE_VERSION
                logging.warning(
                    f"Plugin {module_name} (Core API v{min_core_version}-{max_core_version}) incompatible with Dashboard Core v{DASHBOARD_CORE_VERSION}. Skipping UI registration."
                )
                continue

            # Check manifest for dangerous names (if a manifest exists alongside single .py)
            manifest_path = os.path.join(
                Config.PLUGINS_DIR, module_name, "manifest.json"
            )  # Check for co-located manifest
            if not os.path.exists(manifest_path):  # Check if it's a top-level manifest
                manifest_path = os.path.join(Config.PLUGINS_DIR, f"{module_name}.json")

            if os.path.exists(manifest_path):
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f)
                    if manifest_data.get("name") in DANGEROUS_NAMES or any(
                        dep in DANGEROUS_NAMES for dep in manifest_data.get("dependencies", [])
                    ):
                        logging.warning(
                            f"Manifest for {module_name} contains dangerous names. Skipping."
                        )
                        continue

            if hasattr(module, "register_my_dashboard_panels") and callable(
                module.register_my_dashboard_panels
            ):
                logging.info(f"Invoking register_my_dashboard_panels from plugin: {module_name}")
                module.register_my_dashboard_panels(register_dashboard_panel)
            else:
                logging.info(
                    f"Plugin {module_name} does not have a 'register_my_dashboard_panels' function."
                )

            # Check for UI components in single-file plugins
            title = (
                getattr(module, "TITLE", module_name.capitalize())
                if hasattr(module, "TITLE")
                else module_name.capitalize()
            )
            description = getattr(module, "DESCRIPTION", "")
            roles = getattr(module, "ROLES", [])

            # Allow plugin to register its own locale keys
            if hasattr(module, "register_locale_keys") and callable(module.register_locale_keys):
                module.register_locale_keys(LOCALES)

            if hasattr(module, "render_sidebar_component") and callable(
                module.render_sidebar_component
            ):
                _dashboard_sidebar_components.append(
                    {
                        "plugin": module_name,
                        "title": title,
                        "func": plugin_callback_handler(
                            module.render_sidebar_component
                        ),  # Apply decorator
                        "description": description,
                        "roles": roles,
                    }
                )
                logging.info(f"Registered sidebar component for plugin: {module_name}")
            if hasattr(module, "render_main_component") and callable(module.render_main_component):
                _dashboard_main_components.append(
                    {
                        "plugin": module_name,
                        "title": title,
                        "func": plugin_callback_handler(
                            module.render_main_component
                        ),  # Apply decorator
                        "description": description,
                        "roles": roles,
                    }
                )
                logging.info(f"Registered main component for plugin: {module_name}")
        except Exception as e:
            logging.error(
                f"Error loading or registering dashboard panels from plugin {plugin_file}: {e}",
                exc_info=True,
            )

    # Load directory-based plugins for UI components
    for item in os.listdir(Config.PLUGINS_DIR):
        plugin_path = os.path.join(Config.PLUGINS_DIR, item)
        if not os.path.isdir(plugin_path):
            continue
        if item.startswith(".") or item.startswith("_") or item in DANGEROUS_NAMES:
            continue

        manifest_path = os.path.join(plugin_path, "manifest.json")
        manifest_data = {}
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f)
                    if manifest_data.get("name") in DANGEROUS_NAMES or any(
                        dep in DANGEROUS_NAMES for dep in manifest_data.get("dependencies", [])
                    ):
                        logging.warning(f"Manifest for {item} contains dangerous names. Skipping.")
                        continue
            except json.JSONDecodeError as e:
                logging.error(f"Error reading manifest for {item}: {e}")
                continue

        # Check for API version compatibility based on manifest
        min_core_version = manifest_data.get("min_core_version", "0.0.0")
        max_core_version = manifest_data.get("max_core_version", "999.999.999")
        if not is_version_compatible(DASHBOARD_CORE_VERSION, min_core_version, max_core_version):
            logging.warning(
                f"Plugin {item} (Core API v{min_core_version}-{max_core_version}) incompatible with Dashboard Core v{DASHBOARD_CORE_VERSION}. Skipping UI registration."
            )
            continue

        web_ui_path = os.path.join(plugin_path, "web_ui.py")
        if not os.path.exists(web_ui_path):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"{item}.web_ui", web_ui_path)
            if spec is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"{item}.web_ui"] = module
            spec.loader.exec_module(module)
            if hasattr(module, "register_my_dashboard_panels") and callable(
                module.register_my_dashboard_panels
            ):
                logging.info(f"Invoking register_my_dashboard_panels from web_ui in plugin: {item}")
                module.register_my_dashboard_panels(register_dashboard_panel)
            title = (
                getattr(module, "TITLE", item.capitalize())
                if hasattr(module, "TITLE")
                else item.capitalize()
            )
            description = getattr(module, "DESCRIPTION", "")
            roles = getattr(module, "ROLES", [])

            # Allow plugin to register its own locale keys
            if hasattr(module, "register_locale_keys") and callable(module.register_locale_keys):
                module.register_locale_keys(LOCALES)

            if hasattr(module, "render_sidebar_component") and callable(
                module.render_sidebar_component
            ):
                _dashboard_sidebar_components.append(
                    {
                        "plugin": item,
                        "title": title,
                        "func": plugin_callback_handler(
                            module.render_sidebar_component
                        ),  # Apply decorator
                        "description": description,
                        "roles": roles,
                    }
                )
                logging.info(f"Registered sidebar component for plugin: {item}")
            if hasattr(module, "render_main_component") and callable(module.render_main_component):
                _dashboard_main_components.append(
                    {
                        "plugin": item,
                        "title": title,
                        "func": plugin_callback_handler(
                            module.render_main_component
                        ),  # Apply decorator
                        "description": description,
                        "roles": roles,
                    }
                )
                logging.info(f"Registered main component for plugin: {item}")
        except Exception as e:
            logging.error(f"Error loading web_ui from plugin {item}: {e}", exc_info=True)
    sys.path = original_sys_path
    logging.info("Finished plugin dashboard panel and UI component loading.")


# Helper for version comparison
def is_version_compatible(current_version: str, min_version: str, max_version: str) -> bool:
    """Checks if current_version is within [min_version, max_version] range. Adds fallback without `packaging`."""
    try:
        from packaging.version import parse as parse_version

        current = parse_version(current_version)
        _min = parse_version(min_version)
        _max = parse_version(max_version)
        return _min <= current <= _max
    except ImportError:
        logging.warning(
            "Python 'packaging' library not found. Falling back to simple string comparison."
        )
        # Fallback to simple string comparison if 'packaging' is not available
        return min_version <= current_version <= max_version
    except Exception as e:
        logging.warning(
            f"Error parsing version string with 'packaging': {e}. Falling back to simple string comparison."
        )
        return min_version <= current_version <= max_version


# Load panels if Streamlit is available
if STREAMLIT_AVAILABLE:
    # Call the cached function
    load_plugin_dashboard_panels_cached()

# --- Authentication ---
SECURE_AUTH = False
try:
    import streamlit_authenticator as stauth
    import yaml
    from yaml.loader import SafeLoader

    SECURE_AUTH = True
except ImportError:
    pass


def authenticate_user():
    """
    Gold-standard authentication: Uses streamlit-authenticator if available and secrets present.
    Fallback to demo login for dev/test only.
    """
    if SECURE_AUTH and os.path.exists(".streamlit/auth_config.yaml"):
        with open(".streamlit/auth_config.yaml") as file:
            config = yaml.load(file, Loader=SafeLoader)
        authenticator = stauth.Authenticate(
            config["credentials"],
            config["cookie"]["name"],
            config["cookie"]["key"],
            config["cookie"]["expiry_days"],
            config["preauthorized"],
        )
        name, authentication_status, username = authenticator.login("Login", "main")
        if authentication_status:
            st_dash.session_state.authenticated = True
            st_dash.session_state.user = username
            st_dash.session_state.user_role = config["credentials"]["usernames"][username]["role"]
            authenticator.logout("Logout", "sidebar")
        elif authentication_status is False:
            st_dash.error("Username/password is incorrect")
            st_dash.stop()
        elif authentication_status is None:
            st_dash.warning("Please enter your username and password")
            st_dash.stop()
    else:
        # DEV-ONLY: fallback to demo login
        if "authenticated" not in st_dash.session_state or not st_dash.session_state.authenticated:
            st_dash.session_state.authenticated = False

        if not st_dash.session_state.authenticated:
            st_dash.title("🔐 Please Log In")
            username = st_dash.text_input("Username", key="login_username")
            password = st_dash.text_input("Password", type="password", key="login_password")
            if st_dash.button("Login", key="login_button"):
                if username == "admin" and password == "admin":
                    st_dash.session_state.authenticated = True
                    st_dash.session_state.user = username
                    st_dash.session_state.user_role = "admin"
                    st_dash.rerun()  # Rerun to apply changes
                elif username == "dev" and password == "dev":
                    st_dash.session_state.authenticated = True
                    st_dash.session_state.user = username
                    st_dash.session_state.user_role = "developer"
                    st_dash.rerun()  # Rerun to apply changes
                else:
                    st_dash.error("Invalid credentials.")
            st_dash.stop()


# --- Data Loading and Live Updates ---


@st_dash.cache_data(show_spinner=False)
def load_all_simulation_results(
    results_dir: str = Config.RESULTS_DIR,
) -> List[Dict[str, Any]]:
    all_results = []
    for filepath in glob.glob(os.path.join(results_dir, "*__sim_*.json")):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                result = json.load(f)
            result["_filepath"] = filepath
            all_results.append(result)
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding JSON from {filepath}: {e}")
        except Exception as e:
            logging.error(f"Error reading {filepath}: {e}")
    all_results.sort(
        key=lambda x: os.path.getmtime(x["_filepath"]) if "_filepath" in x else 0,
        reverse=True,
    )
    return all_results


def get_live_data(job_id: str) -> Optional[Dict[str, Any]]:
    if not REDIS_AVAILABLE or not redis_client:
        return None
    try:
        data = redis_client.get(f"sim_progress:{job_id}")
        if data:
            return json.loads(data)
    except Exception as e:
        logging.error(f"Error fetching live data for {job_id}: {e}")
    return None


def listen_for_live_updates(job_id: str, update_callback: Callable[[Dict[str, Any]], None]):
    if not REDIS_AVAILABLE or not redis_client:
        return
    pubsub = redis_client.pubsub()
    channel = f"sim_progress:{job_id}"
    pubsub.subscribe(channel)
    try:
        for msg in pubsub.listen():
            if msg["type"] == "message":
                data = json.loads(msg["data"])
                update_callback(data)
    except Exception as e:
        logging.error(f"Live update listener error: {e}")


# --- Metrics Display ---


def display_core_metrics(selected_result: Dict[str, Any]):
    col1, col2, col3 = st_dash.columns(3)
    with col1:
        total_runs = selected_result.get("runs")
        if total_runs is not None and isinstance(total_runs, list):
            st_dash.metric("Total Runs", len(total_runs))
        else:
            st_dash.metric("Total Runs", "N/A")

        failed_runs_count = sum(
            1
            for r in selected_result.get("runs", [])
            if isinstance(r, dict) and r.get("returncode") != 0
        )
        st_dash.metric("Failed Runs", failed_runs_count)
    with col2:
        coverage = selected_result.get("coverage", {}).get("coverage")
        if coverage is not None and isinstance(coverage, (int, float)):
            st_dash.metric("Code Coverage", f"{coverage*100:.1f}%")
        else:
            st_dash.metric("Code Coverage", "N/A")

        rl_reward = selected_result.get("rl_reward")
        if rl_reward is not None and isinstance(rl_reward, (int, float)):
            st_dash.metric("RL Reward", f"{rl_reward:.2f}")
        else:
            st_dash.metric("RL Reward", "N/A")
    with col3:
        ethics_compliant = selected_result.get("ethics", {}).get("compliant")
        if ethics_compliant is not None:
            st_dash.metric("Ethical Compliance", "✅ Yes" if ethics_compliant else "❌ No")
        else:
            st_dash.metric("Ethical Compliance", "N/A")

        energy_kwh = selected_result.get("sustainability", {}).get("energy_kwh")
        if energy_kwh is not None and isinstance(energy_kwh, (int, float)):
            st_dash.metric("Energy Consumed", f"{energy_kwh:.3f} kWh")
        else:
            st_dash.metric("Energy Consumed", "N/A")


# --- Workflow Visualization (Optional) ---
WORKFLOW_VIZ_AVAILABLE = False
try:
    # workflow_viz needs to be importable; assuming it's in the same directory or sys.path
    from workflow_viz import render_workflow_viz

    WORKFLOW_VIZ_AVAILABLE = True
except ImportError:
    logging.warning(
        "Workflow visualization (workflow_viz.py) not found. This feature will be unavailable."
    )


def _display_summary_and_details(result):
    """Shared summary/expander for both Matplotlib and Plotly modes."""
    findings = result.get("findings", [])
    actions = result.get("actions", [])
    status = result.get("status", "UNKNOWN")
    scores = result.get("review", {}).get("scores", {})
    coverage = scores.get("coverage", "N/A")

    st_dash.markdown(
        f"""
    ### Workflow Summary
    - **Status:** `{status}`
    - **Coverage:** `{coverage}`
    - **Security Findings:** `{len(findings)}`
    - **Actions Taken:** `{len(actions)}`
    """
    )
    with st_dash.expander("🔎 Detailed Findings & Actions", expanded=False):
        st_dash.subheader("Findings")
        if findings:
            for i, finding in enumerate(findings, 1):
                st_dash.markdown(f"- **{i}.** {finding}")
        else:
            st_dash.info("No findings detected.")
        st_dash.subheader("Actions Taken")
        if actions:
            for i, action in enumerate(actions, 1):
                st_dash.markdown(f"- **{i}.** {action}")
        else:
            st_dash.info("No refinements needed.")


# --- Plugin Gallery Display ---


def display_plugin_gallery(plugin_manager: PluginManager, user_role: str):
    st_dash.header("🔌 Plugin Gallery / Marketplace")

    if not PLUGIN_MANAGER_AVAILABLE:
        st_dash.error("PluginManager is not available. Cannot manage plugins.")
        return

    # Force a reload of plugin list for fresh status
    all_plugins = plugin_manager.list_plugins()

    if not all_plugins:
        st_dash.info("No plugins discovered in the plugins directory.")
        return

    error_plugins = [p for p in all_plugins if p.get("status") == "error"]
    if error_plugins:
        st_dash.warning(f"{len(error_plugins)} plugin(s) failed to load:")
        for ep in error_plugins:
            st_dash.error(
                f"Plugin: {ep.get('name', 'Unnamed')} — {ep.get('error', 'Unknown error')}"
            )

    for plugin in all_plugins:
        name = plugin.get("name", "Unnamed Plugin")
        status = plugin.get("status", "unknown")
        manifest = plugin.get("manifest", {})
        error = plugin.get("error", "")

        with st_dash.expander(f"**{name}** (Status: `{status}`)", expanded=False):
            st_dash.markdown(f"**Version:** `{manifest.get('version', 'N/A')}`")
            st_dash.markdown(f"**Type:** `{manifest.get('type', 'N/A')}`")
            st_dash.markdown(
                f"**Description:** {manifest.get('description', 'No description provided.')}"
            )
            st_dash.markdown(f"**Entrypoint:** `{manifest.get('entrypoint', 'N/A')}`")
            st_dash.markdown(f"**Health Check Method:** `{manifest.get('health_check', 'N/A')}`")
            st_dash.markdown(f"**API Version:** `{manifest.get('api_version', 'N/A')}`")

            # Display core version compatibility
            min_core_ver = manifest.get("min_core_version", "N/A")
            max_core_ver = manifest.get("max_core_version", "N/A")
            st_dash.markdown(f"**Core API Compatibility:** `{min_core_ver}` - `{max_core_ver}`")
            if not is_version_compatible(DASHBOARD_CORE_VERSION, min_core_ver, max_core_ver):
                st_dash.warning(
                    f"Plugin might be incompatible with Dashboard Core v{DASHBOARD_CORE_VERSION}."
                )

            st_dash.markdown(
                f"**Permissions:** `{', '.join(manifest.get('permissions', ['None']))}`"
            )
            st_dash.markdown(
                f"**Capabilities:** `{', '.join(manifest.get('capabilities', ['None']))}`"
            )
            st_dash.markdown(f"**Author:** `{manifest.get('author', 'N/A')}`")
            st_dash.markdown(f"**Homepage:** `{manifest.get('homepage', 'N/A')}`")
            st_dash.markdown(f"**Tags:** `{', '.join(manifest.get('tags', ['None']))}`")

            if error:
                st_dash.error(f"**Error:** {error}")

            col_buttons = st_dash.columns(4)

            # Enable/Disable buttons
            if user_role == "admin":
                if status == "disabled" or status == "error":
                    if col_buttons[0].button(
                        f"✅ Enable {name}",
                        key=f"enable_{name}",
                        help=t("enable_plugin_tooltip"),
                    ):
                        try:
                            plugin_manager.enable_plugin(name)
                            st_dash.success(f"Plugin '{name}' enabled.")
                            st_dash.rerun()
                        except Exception as e:
                            st_dash.error(f"Failed to enable '{name}': {e}")
                elif status == "loaded":
                    if col_buttons[0].button(
                        f"❌ Disable {name}",
                        key=f"disable_{name}",
                        help=t("disable_plugin_tooltip"),
                    ):
                        try:
                            plugin_manager.disable_plugin(name)
                            st_dash.success(f"Plugin '{name}' disabled.")
                            st_dash.rerun()
                        except Exception as e:
                            st_dash.error(f"Failed to disable '{name}': {e}")
            else:
                col_buttons[0].info(t("admin_role_required_info"))

            # Reload button (primarily for Python plugins)
            if manifest.get("type") == "python" and user_role == "admin":
                if col_buttons[1].button(
                    f"🔄 Reload {name}",
                    key=f"reload_{name}",
                    help=t("reload_plugin_tooltip"),
                ):
                    try:
                        plugin_manager.reload_plugin(name)
                        st_dash.success(f"Plugin '{name}' reloaded.")
                        st_dash.rerun()
                    except Exception as e:
                        st_dash.error(f"Failed to reload '{name}': {e}")
            elif manifest.get("type") == "python":
                col_buttons[1].info(t("reload_python_plugin_info"))
            else:
                col_buttons[1].info(t("reload_not_applicable_info"))

            # Health Check button
            if col_buttons[2].button(
                f"❤️ Health Check {name}",
                key=f"health_{name}",
                help=t("run_health_check_tooltip"),
            ):
                try:
                    # Async health check needs to be run in a way compatible with Streamlit's event loop
                    health_status = run_async_streamlit(plugin_manager.plugin_health(name))
                    st_dash.json(health_status)
                    if health_status.get("status") != "ok" and user_role == "admin":
                        if st_dash.button(
                            f"Reset Plugin {name}",
                            key=f"reset_plugin_{name}",
                            help=t("reset_plugin_tooltip"),
                        ):
                            # Conceptual reset: For Python, could involve deleting .pyc and re-enabling.
                            # For others, it might mean reinstalling or clearing state.
                            # For now, just a message.
                            st_dash.info(
                                f"Conceptual reset initiated for {name}. Actual reset logic needs implementation."
                            )
                            # Example of a reload after conceptual reset
                            try:
                                plugin_manager.disable_plugin(name)
                                time.sleep(0.1)  # brief pause
                                plugin_manager.enable_plugin(name)
                                st_dash.success(
                                    f"Plugin '{name}' re-enabled after conceptual reset."
                                )
                                st_dash.rerun()
                            except Exception as re:
                                st_dash.error(
                                    f"Error during re-enable after conceptual reset: {re}"
                                )
                except Exception as e:
                    st_dash.error(f"Health check failed for '{name}': {e}")
                    st_dash.exception(e)  # Show full traceback
                    if user_role == "admin":
                        if st_dash.button(
                            f"Reset Plugin {name}",
                            key=f"reset_plugin_fail_{name}",
                            help=t("reset_plugin_tooltip"),
                        ):
                            st_dash.info(
                                f"Conceptual reset initiated for {name}. Actual reset logic needs implementation."
                            )
                            try:
                                plugin_manager.disable_plugin(name)
                                time.sleep(0.1)
                                plugin_manager.enable_plugin(name)
                                st_dash.success(
                                    f"Plugin '{name}' re-enabled after conceptual reset."
                                )
                                st_dash.rerun()
                            except Exception as re:
                                st_dash.error(
                                    f"Error during re-enable after conceptual reset: {re}"
                                )

            # Placeholder for "Update" button (conceptual, requires update logic)
            # if col_buttons[3].button(f"⬆️ Check for Updates", key=f"update_{name}"):
            #        st_dash.info(f"Checking for updates for {name} (feature not yet implemented).")


# --- Onboarding Functions (adapted from onboard.py) ---
def _generate_config_gui(config_data: Dict[str, Any], filename: str = "config.json"):
    """Writes the generated configuration to a JSON file (GUI version)."""
    config_path = os.path.join(Config.CONFIG_DIR, filename)
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, sort_keys=True)
        st_dash.success(f"Generated configuration file: {config_path}")
        logging.info(f"Generated configuration file: {config_path}")
    except Exception as e:
        st_dash.error(f"Failed to write config file {config_path}: {e}")
        logging.exception(f"Failed to write config file {config_path}: {e}")


def sanitize_plugin_name(plugin_name: str) -> str:
    """Sanitize plugin name to prevent path traversal or invalid characters."""
    plugin_name = os.path.basename(plugin_name)  # Strip any path components
    if ".." in plugin_name or "/" in plugin_name or "\\" in plugin_name:
        raise ValueError("Path traversal in plugin name not allowed")
    plugin_name = re.sub(
        r"[^a-zA-Z0-9_]", "", plugin_name
    )  # Allow only alphanumeric and underscore
    if not plugin_name or plugin_name[0].isdigit() or plugin_name in DANGEROUS_NAMES:
        raise ValueError(f"Invalid plugin name: {plugin_name}")
    if plugin_name in sys.modules:
        raise ValueError(f"Plugin name '{plugin_name}' collides with existing module")
    if any(c in plugin_name for c in (".", "/", "\\")):
        raise ValueError("Plugin name cannot contain dot or path separator")
    return plugin_name


def _generate_plugin_manifest_gui(plugin_type: str, plugin_name: str, plugins_dir: str):
    """Generates a basic plugin manifest and a dummy plugin file (GUI version)."""
    try:
        plugin_name = sanitize_plugin_name(plugin_name)
    except ValueError as e:
        st_dash.error(str(e))
        logging.error(str(e))
        return

    manifest = {
        "name": plugin_name,
        "version": "0.0.1",
        "description": f"A demo {plugin_type} plugin generated by the dashboard.",
        "entrypoint": "__init__.py" if plugin_type == "python" else "main",
        "type": plugin_type,
        "author": "Omnisapient Dashboard",
        "capabilities": ["demo_capability"],
        "permissions": ["none"],
        "dependencies": [],
        "min_core_version": "1.1.0",  # Pinning to current example
        "max_core_version": "2.0.0",  # Pinning to current example
        "health_check": "plugin_health",
        "api_version": "v1",
        "license": "MIT",
        "homepage": "",
        "tags": ["demo", "onboarding"],
        "generated_with": {
            "wizard_version": "1.0.0",
            "python_version": platform.python_version(),
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }

    # Enforce DANGEROUS_NAMES in generated manifest
    if manifest["name"] in DANGEROUS_NAMES:
        st_dash.error(
            f"Generated plugin name '{plugin_name}' is a dangerous name. Please choose another."
        )
        logging.error(f"Attempted to generate plugin with dangerous name: {plugin_name}")
        return

    plugin_dir = os.path.join(plugins_dir, plugin_name)
    os.makedirs(plugin_dir, exist_ok=True)
    manifest_path = os.path.join(plugin_dir, "manifest.json")
    try:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        st_dash.info(f"Generated demo {plugin_type} plugin manifest: {manifest_path}")
        logging.info(f"Generated demo {plugin_type} plugin manifest: {manifest_path}")
    except Exception as e:
        st_dash.error(f"Failed to write demo plugin manifest {manifest_path}: {e}")
        logging.exception(f"Failed to write demo plugin manifest {manifest_path}: {e}")

    if plugin_type == "python":
        plugin_file_content = f"""# --- Demo Python Plugin Generated by Dashboard ---
PLUGIN_MANIFEST = {json.dumps(manifest, indent=4)}

def plugin_health():
    return {{"status": "ok", "message": "Demo Python plugin is healthy!"}}

class PLUGIN_API:
    def hello(self):
        return "Hello from the dashboard demo Python plugin!"

def register_locale_keys(locales_dict):
    # Example of how a plugin can register its own locale keys
    locales_dict.get("en", {{}})["demo_plugin_message"] = "This is a message from the demo plugin."
    locales_dict.get("es", {{}})["demo_plugin_message"] = "Este es un mensaje del plugin de demostración."

# Example of a dashboard panel registration within a plugin
def render_demo_panel(st_obj, selected_result):
    st_obj.subheader(st_obj.session_state.get('LOCALES', {{}}).get(st_obj.session_state.get('lang', 'en'), {{}}).get('demo_plugin_message', 'Demo Plugin Panel'))
    st_obj.write("This panel is rendered by the demo plugin.")
    if selected_result:
        st_obj.json(selected_result.get('metrics', {{}}))

if __name__ == "__main__":
    import json
    print(json.dumps(PLUGIN_MANIFEST, indent=4))
    print(plugin_health())
    print(PLUGIN_API().hello())
"""
        plugin_filepath = os.path.join(plugin_dir, "__init__.py")
        try:
            with open(plugin_filepath, "w", encoding="utf-8") as f:
                f.write(plugin_file_content)
            st_dash.info(f"Generated demo Python plugin file: {plugin_filepath}")
            logging.info(f"Generated demo Python plugin file: {plugin_filepath}")
        except Exception as e:
            st_dash.error(f"Failed to write demo plugin file {plugin_filepath}: {e}")
            logging.exception(f"Failed to write demo plugin file {plugin_filepath}: {e}")
    elif plugin_type == "wasm":
        wasm_filepath = os.path.join(plugin_dir, f"{plugin_name}.wasm")
        with open(wasm_filepath, "wb") as f:
            f.write(b"\x00\x61\x73\x6d\x01\x00\x00\x00")  # Minimal valid WASM header
        st_dash.info(
            f"Created placeholder WASM file: {wasm_filepath}. Replace with your compiled .wasm binary."
        )

    # Generate example web_ui.py for all plugin types
    web_ui_content = f"""# web_ui.py for {plugin_name} ({plugin_type})
import streamlit as st

TITLE = "{plugin_name.capitalize()} UI"
DESCRIPTION = "UI components for the {plugin_type} plugin"
ROLES = ["admin", "developer"]

def render_sidebar_component(sidebar):
    sidebar.write("Sidebar controls for {plugin_name}")
    sidebar.button("Action", key="sidebar_action_{plugin_name}")

def render_main_component(main):
    main.write("Main UI for {plugin_name}")
    main.info("Customize this UI as needed.")
"""
    web_ui_filepath = os.path.join(plugin_dir, "web_ui.py")
    try:
        with open(web_ui_filepath, "w", encoding="utf-8") as f:
            f.write(web_ui_content)
        st_dash.info(f"Generated example web_ui.py: {web_ui_filepath}")
        logging.info(f"Generated example web_ui.py: {web_ui_filepath}")
    except Exception as e:
        st_dash.error(f"Failed to write web_ui.py {web_ui_filepath}: {e}")
        logging.exception(f"Failed to write web_ui.py {web_ui_filepath}: {e}")


def run_async_streamlit(coroutine):
    """
    Runs an asyncio coroutine in a way compatible with Streamlit.
    Handles cases where an event loop might already be running (e.g., due to nest_asyncio).
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        # If loop is already running, schedule the coroutine as a task
        # and wait for it. This is tricky in Streamlit's single-threaded nature.
        # For short-lived coroutines like health checks, we can poll.
        task = loop.create_task(coroutine)
        while not task.done():
            time.sleep(0.01)  # Small sleep to yield control
        return task.result()
    else:
        return loop.run_until_complete(coroutine)


async def _run_health_checks_gui(config: Dict[str, Any], test_all_plugins: bool = False):
    """Runs health checks for configured backends and optionally all plugins (GUI version)."""
    st_dash.subheader("Backend Health Check Results")
    failed_checks = []

    # Pub/Sub Health Check
    if ONBOARDING_BACKENDS_AVAILABLE:
        pubsub_backend_url = config.get("notification_backend", {}).get("url")
        if pubsub_backend_url:
            st_dash.write(f"Checking Pub/Sub backend: `{pubsub_backend_url}`")
            try:
                mesh_kwargs = {}
                if pubsub_backend_url.startswith("gcs://"):
                    mesh_kwargs["gcs_bucket_name"] = config["notification_backend"].get(
                        "gcs_bucket_name"
                    )
                elif pubsub_backend_url.startswith("azure://"):
                    mesh_kwargs["azure_connection_string"] = config["notification_backend"].get(
                        "azure_connection_string"
                    )
                    mesh_kwargs["azure_container_name"] = config["notification_backend"].get(
                        "azure_container_name"
                    )
                elif pubsub_backend_url.startswith("etcd://"):
                    mesh_kwargs["etcd_host"] = config["notification_backend"].get("etcd_host")
                    mesh_kwargs["etcd_port"] = int(
                        config["notification_backend"].get("etcd_port", 2379)
                    )

                mesh = MeshPubSub(backend_url=pubsub_backend_url, **mesh_kwargs)
                await mesh.connect()
                health = await mesh.healthcheck()
                st_dash.success(
                    f"Pub/Sub Health: {health['status'].upper()} - {health.get('message', '')}"
                )
                await mesh.close()
            except Exception as e:
                st_dash.error(f"Pub/Sub Health Check FAILED: {e}")
                logging.error(f"Pub/Sub Health Check FAILED: {e}")
                failed_checks.append("Pub/Sub")
        else:
            st_dash.info("No Pub/Sub backend configured.")
    else:
        st_dash.warning(
            "Required backend modules for Pub/Sub not available. Cannot perform health check."
        )

    # Checkpoint Health Check
    if ONBOARDING_BACKENDS_AVAILABLE:
        checkpoint_backend_type = config.get("checkpoint_backend", {}).get("type")
        if checkpoint_backend_type:
            st_dash.write(f"Checking Checkpoint backend: `{checkpoint_backend_type}`")
            try:
                chk_manager_kwargs = {"backend": checkpoint_backend_type}
                backend_config_key = f"{checkpoint_backend_type}_config"
                if backend_config_key in config["checkpoint_backend"]:
                    chk_manager_kwargs[backend_config_key] = config["checkpoint_backend"][
                        backend_config_key
                    ]

                if checkpoint_backend_type == "fs":
                    fs_dir = config["checkpoint_backend"].get("dir", "./checkpoints")
                    os.makedirs(fs_dir, exist_ok=True)
                    chk_manager_kwargs["dir"] = fs_dir

                chk = CheckpointManager(**chk_manager_kwargs)

                test_data = {"status": "healthy", "timestamp": time.time()}
                test_name = "onboarding_health_test"

                try:
                    await chk.delete(test_name)  # Clean up previous test data
                except Exception:
                    pass  # Ignore if not found

                await chk.save(test_name, test_data)
                loaded_data = await chk.load(test_name)
                await chk.delete(test_name)  # Clean up test data

                if loaded_data and loaded_data.get("status") == "healthy":
                    st_dash.success(
                        f"Checkpoint Health: OK (saved and loaded test data successfully for {checkpoint_backend_type})."
                    )
                else:
                    st_dash.error(
                        f"Checkpoint Health FAILED: Data mismatch for {checkpoint_backend_type}."
                    )
                    failed_checks.append("Checkpoint")
            except Exception as e:
                st_dash.error(f"Checkpoint Health Check FAILED for {checkpoint_backend_type}: {e}")
                logging.error(f"Checkpoint Health Check FAILED for {checkpoint_backend_type}: {e}")
                failed_checks.append("Checkpoint")
        else:
            st_dash.info("No Checkpoint backend configured.")
    else:
        st_dash.warning(
            "Required backend modules for Checkpointing not available. Cannot perform health check."
        )

    if test_all_plugins and PLUGIN_MANAGER_AVAILABLE:
        st_dash.subheader("Plugin Health Check Results")
        plugin_manager = st_dash.session_state.plugin_manager_instance  # Use the cached instance

        # Reload plugins to ensure latest state after potential onboarding generations
        plugin_manager.load_all()
        all_plugins = plugin_manager.list_plugins()

        plugin_health_results = {}
        for p in all_plugins:
            name = p.get("name", "Unnamed")
            status = p.get("status", "unknown")
            st_dash.write(f"Checking plugin: `{name}` (Status: `{status}`)")
            if status == "loaded":
                try:
                    health_status = await plugin_manager.plugin_health(name)
                    plugin_health_results[name] = health_status
                    if health_status.get("status") == "ok":
                        st_dash.success(
                            f"Plugin '{name}' Health: OK - {health_status.get('message', '')}"
                        )
                    else:
                        st_dash.warning(
                            f"Plugin '{name}' Health: {health_status.get('status', 'FAIL').upper()} - {health_status.get('message', '')}"
                        )
                        failed_checks.append(f"Plugin: {name}")
                except Exception as e:
                    plugin_health_results[name] = {
                        "status": "error",
                        "message": str(e),
                        "traceback": traceback.format_exc(),
                    }
                    st_dash.error(f"Plugin '{name}' Health Check FAILED: {e}")
                    logging.error(f"Plugin '{name}' Health Check FAILED: {e}", exc_info=True)
                    failed_checks.append(f"Plugin: {name}")
            else:
                st_dash.info(
                    f"Skipping health check for plugin '{name}' as it is not loaded (Status: {status})."
                )

        if plugin_health_results:
            with st_dash.expander("Detailed Plugin Health Report"):
                st_dash.json(plugin_health_results)

    if failed_checks:
        st_dash.error(f"Summary: The following checks failed: {', '.join(failed_checks)}")
    else:
        st_dash.success("All configured health checks passed!")


def display_onboarding_wizard():
    st_dash.header("🚀 Project Onboarding Wizard")
    st_dash.markdown("Configure your project, set up backends, and generate starter files.")

    with st_dash.form("onboarding_form"):
        st_dash.subheader("1. Project Type")
        project_type = st_dash.selectbox(
            "What type of project are you building?",
            options=["agentic_swarm", "simulation", "rl_environment", "other"],
            index=0,
            key="onboard_project_type",
            help=t("project_type_tooltip"),
        )

        st_dash.subheader("2. Plugin Configuration")
        plugin_types_options = ["python", "wasm", "grpc"]
        selected_plugin_types = st_dash.multiselect(
            "Which plugin types do you plan to use?",
            options=plugin_types_options,
            default=["python"],
            key="onboard_plugin_types",
            help=t("plugin_types_tooltip"),
        )
        if not selected_plugin_types:
            st_dash.warning("Please select at least one plugin type to generate a demo plugin.")

        st_dash.subheader("3. Notification Backend (Pub/Sub)")
        pubsub_backend_options = ["local"]
        if ONBOARDING_BACKENDS_AVAILABLE:
            try:
                pubsub_backend_options.extend(
                    [b for b in MeshPubSub.supported_backends() if b != "local"]
                )
                pubsub_backend_options = sorted(list(set(pubsub_backend_options)))
            except Exception:
                # Fallback list if supported_backends() fails
                pubsub_backend_options.extend(
                    [
                        "redis",
                        "nats",
                        "kafka",
                        "rabbitmq",
                        "aws",
                        "gcs",
                        "azure",
                        "etcd",
                    ]
                )
                pubsub_backend_options = sorted(list(set(pubsub_backend_options)))

        pubsub_backend = st_dash.selectbox(
            "Choose your preferred notification backend:",
            options=pubsub_backend_options,
            index=(
                pubsub_backend_options.index("redis") if "redis" in pubsub_backend_options else 0
            ),
            key="onboard_pubsub_backend",
            help=t("pubsub_backend_tooltip"),
        )
        pubsub_config = {"type": pubsub_backend}
        if pubsub_backend == "redis":
            pubsub_config["url"] = st_dash.text_input(
                "Redis URL",
                value="redis://localhost:6379/0",
                key="pubsub_redis_url",
                help=t("redis_url_tooltip"),
            )
        elif pubsub_backend == "nats":
            pubsub_config["url"] = st_dash.text_input(
                "NATS URL",
                value="nats://localhost:4222",
                key="pubsub_nats_url",
                help=t("nats_url_tooltip"),
            )
        elif pubsub_backend == "kafka":
            pubsub_config["url"] = st_dash.text_input(
                "Kafka Bootstrap Servers (e.g., localhost:9092)",
                value="localhost:9092",
                key="pubsub_kafka_url",
                help=t("kafka_url_tooltip"),
            )
        elif pubsub_backend == "rabbitmq":
            pubsub_config["url"] = st_dash.text_input(
                "RabbitMQ AMQP URL",
                value="amqp://guest:guest@localhost:5672/",
                key="pubsub_rabbitmq_url",
                help=t("rabbitmq_url_tooltip"),
            )
        elif pubsub_backend == "aws":
            pubsub_config["url"] = "aws://"
            st_dash.info(
                "For AWS backend, ensure AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION_NAME are set as environment variables."
            )
        elif pubsub_backend == "gcs":
            pubsub_config["url"] = "gcs://"
            pubsub_config["gcs_bucket_name"] = st_dash.text_input(
                "GCS Bucket Name for events",
                value="your-gcs-event-bucket",
                key="pubsub_gcs_bucket",
                help=t("gcs_bucket_tooltip"),
            )
            st_dash.info(
                "For GCS backend, ensure GOOGLE_APPLICATION_CREDENTIALS points to your service account key."
            )
        elif pubsub_backend == "azure":
            pubsub_config["url"] = "azure://"
            pubsub_config["azure_connection_string"] = st_dash.text_input(
                "Azure Storage Connection String",
                value="DefaultEndpointsProtocol=...",
                key="pubsub_azure_conn_str",
                help=t("azure_conn_str_tooltip"),
            )
            pubsub_config["azure_container_name"] = st_dash.text_input(
                "Azure Blob Container Name for events",
                value="mesh-events",
                key="pubsub_azure_container",
                help=t("azure_container_tooltip"),
            )
        elif pubsub_backend == "etcd":
            pubsub_config["url"] = "etcd://"
            pubsub_config["etcd_host"] = st_dash.text_input(
                "etcd Host",
                value="localhost",
                key="pubsub_etcd_host",
                help=t("etcd_host_tooltip"),
            )
            pubsub_config["etcd_port"] = st_dash.text_input(
                "etcd Port",
                value="2379",
                key="pubsub_etcd_port",
                help=t("etcd_port_tooltip"),
            )
        elif pubsub_backend == "local":
            pubsub_config["url"] = "local://"

        st_dash.subheader("4. Checkpoint Backend (State Persistence)")
        checkpoint_backend_options = ["fs"]
        if ONBOARDING_BACKENDS_AVAILABLE:
            try:
                checkpoint_backend_options.extend(
                    [b for b in CheckpointManager._BACKENDS.keys() if b != "fs"]
                )
                checkpoint_backend_options = sorted(list(set(checkpoint_backend_options)))
            except Exception:
                # Fallback list if _BACKENDS is not directly accessible or fails
                checkpoint_backend_options.extend(
                    ["s3", "redis", "postgres", "gcs", "azure", "etcd"]
                )
                checkpoint_backend_options = sorted(list(set(checkpoint_backend_options)))

        checkpoint_backend = st_dash.selectbox(
            "Choose your preferred checkpoint backend:",
            options=checkpoint_backend_options,
            index=(
                checkpoint_backend_options.index("fs") if "fs" in checkpoint_backend_options else 0
            ),
            key="onboard_checkpoint_backend",
            help=t("checkpoint_backend_tooltip"),
        )
        checkpoint_config = {"type": checkpoint_backend}
        if checkpoint_backend == "fs":
            checkpoint_config["dir"] = st_dash.text_input(
                "Local directory for checkpoints",
                value="./checkpoints",
                key="chk_fs_dir",
                help=t("fs_dir_tooltip"),
            )
        elif checkpoint_backend == "s3":
            checkpoint_config["s3_config"] = {
                "bucket": st_dash.text_input(
                    "S3 Bucket Name",
                    value="your-s3-checkpoint-bucket",
                    key="chk_s3_bucket",
                    help=t("s3_bucket_tooltip"),
                )
            }
            st_dash.info(
                "For S3 backend, ensure AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION_NAME are set as environment variables."
            )
        elif checkpoint_backend == "redis":
            checkpoint_config["redis_config"] = {
                "url": st_dash.text_input(
                    "Redis URL for checkpoints",
                    value="redis://localhost:6379/1",
                    key="chk_redis_url",
                    help=t("chk_redis_url_tooltip"),
                )
            }
        elif checkpoint_backend == "postgres":
            checkpoint_config["postgres_config"] = {
                "dsn": st_dash.text_input(
                    "Postgres DSN",
                    value="postgresql://user:password@localhost:5432/database",
                    key="chk_pg_dsn",
                    help=t("pg_dsn_tooltip"),
                )
            }
            st_dash.info("Ensure your Postgres database has the 'checkpoints' table created.")
        elif checkpoint_backend == "gcs":
            checkpoint_config["gcs_config"] = {
                "bucket": st_dash.text_input(
                    "GCS Bucket Name for checkpoints",
                    value="your-gcs-checkpoint-bucket",
                    key="chk_gcs_bucket",
                    help=t("chk_gcs_bucket_tooltip"),
                )
            }
            st_dash.info(
                "For GCS backend, ensure GOOGLE_APPLICATION_CREDENTIALS points to your service account key."
            )
        elif checkpoint_backend == "azure":
            checkpoint_config["azure_config"] = {
                "connection_string": st_dash.text_input(
                    "Azure Storage Connection String for checkpoints",
                    value="DefaultEndpointsProtocol=...",
                    key="chk_azure_conn_str",
                    help=t("chk_azure_conn_str_tooltip"),
                ),
                "container_name": st_dash.text_input(
                    "Azure Blob Container Name for checkpoints",
                    value="checkpoints",
                    key="chk_azure_container",
                    help=t("chk_azure_container_tooltip"),
                ),
            }
        elif checkpoint_backend == "etcd":
            checkpoint_config["etcd_config"] = {
                "host": st_dash.text_input(
                    "etcd Host for checkpoints",
                    value="localhost",
                    key="chk_etcd_host",
                    help=t("chk_etcd_host_tooltip"),
                ),
                "port": st_dash.text_input(
                    "etcd Port for checkpoints",
                    value="2379",
                    key="chk_etcd_port",
                    help=t("chk_etcd_port_tooltip"),
                ),
            }

        submitted = st_dash.form_submit_button(
            "Complete Onboarding & Generate Config", help=t("generate_config_tooltip")
        )
        if submitted:
            st_dash.session_state.onboarding_config = {
                "project_type": project_type,
                "plugins_dir": Config.PLUGINS_DIR,  # Use Config.PLUGINS_DIR
                "results_dir": Config.RESULTS_DIR,  # Use Config.RESULTS_DIR
                "notification_backend": pubsub_config,
                "checkpoint_backend": checkpoint_config,
                "selected_plugin_types": selected_plugin_types,  # Store for generating demo plugins
                "environment_variables": {  # For display purposes
                    "MESH_BACKEND_URL": pubsub_config.get("url", ""),
                    "CHECKPOINT_BACKEND_TYPE": checkpoint_config.get("type", ""),
                    "CHECKPOINT_FS_DIR": checkpoint_config.get("dir", ""),
                },
                "generated_with": {
                    "wizard_version": "1.0.0",
                    "python_version": platform.python_version(),
                    "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            }
            _generate_config_gui(st_dash.session_state.onboarding_config)

            for p_type in selected_plugin_types:
                plugin_name = f"demo_{p_type}_plugin"
                _generate_plugin_manifest_gui(
                    p_type, plugin_name, Config.PLUGINS_DIR
                )  # Use Config.PLUGINS_DIR

            st_dash.success("Onboarding complete! Configuration and demo plugins generated.")
            st_dash.info("You can now run health checks or a demo job.")
            st_dash.session_state.onboarding_completed = True
            st_dash.session_state.generated_config_path = os.path.join(
                Config.CONFIG_DIR, "config.json"
            )  # Use Config.CONFIG_DIR
            st_dash.session_state.generated_plugins = [
                f"demo_{p_type}_plugin" for p_type in selected_plugin_types
            ]
            st_dash.rerun()  # Rerun to show the new buttons

    if st_dash.session_state.get("onboarding_completed", False):
        st_dash.subheader("Onboarding Actions")
        col_onboard_buttons = st_dash.columns(2)
        if col_onboard_buttons[0].button(
            "Run All Health Checks",
            key="run_all_health_checks",
            help=t("run_all_health_checks_tooltip"),
        ):
            if "onboarding_config" in st_dash.session_state:
                with st_dash.spinner("Running health checks... This might take a moment."):
                    run_async_streamlit(
                        _run_health_checks_gui(
                            st_dash.session_state.onboarding_config,
                            test_all_plugins=True,
                        )
                    )
                st_dash.success("Health checks completed. See results above.")
            else:
                st_dash.error(
                    "Onboarding configuration not found. Please complete onboarding first."
                )

        if col_onboard_buttons[1].button(
            "Run Demo Job (Conceptual)",
            key="run_demo_job",
            help=t("run_demo_job_tooltip"),
        ):
            st_dash.info(
                "Running a demo job is conceptual and requires your main simulation script."
            )
            st_dash.code(
                f"python your_main_simulation_script.py --config {os.path.join(Config.CONFIG_DIR, 'config.json')}",
                language="bash",
            )  # Use Config.CONFIG_DIR
            st_dash.success("Demo job command displayed. Execute it in your terminal.")

        st_dash.subheader("Generated Files")
        if "generated_config_path" in st_dash.session_state:
            st_dash.markdown(
                f"View generated config: `{st_dash.session_state.generated_config_path}`"
            )
            with open(st_dash.session_state.generated_config_path, "r") as f:
                config_data = f.read()
            st_dash.download_button(
                "Download Config",
                config_data,
                file_name="config.json",
                mime="application/json",
                key="download_config_btn",
                help=t("download_config_tooltip"),
            )

        if "generated_plugins" in st_dash.session_state:
            for plugin in st_dash.session_state.generated_plugins:
                st_dash.markdown(f"Generated plugin: `{plugin}`")


# --- Main Dashboard Function ---

# Define minimal LOCALES for demonstration. In a real app, load from JSON/YAML.
LOCALES = {
    "en": {
        "welcome_message": "Welcome",
        "settings_header": "Settings",
        "language_selector_label": "Language",
        "high_contrast_mode_label": "High Contrast Mode",
        "dark_mode_label": "Dark Mode",
    },
    "es": {
        "welcome_message": "Bienvenido",
        "settings_header": "Ajustes",
        "language_selector_label": "Idioma",
        "high_contrast_mode_label": "Modo de alto contraste",
        "dark_mode_label": "Modo oscuro",
    },
}


def t(key: str) -> str:
    """Translation function with fallback to key if not found."""
    # Ensure st_dash.session_state.lang is initialized
    if "lang" not in st_dash.session_state:
        st_dash.session_state.lang = "en"

    # Store LOCALES in session_state so plugins can access it (if they register their own keys)
    if "LOCALES" not in st_dash.session_state:
        st_dash.session_state.LOCALES = LOCALES

    return st_dash.session_state.LOCALES.get(
        st_dash.session_state.lang, st_dash.session_state.LOCALES["en"]
    ).get(key, key)


def display_simulation_dashboard():
    # Set page config at the very top, before any st.* calls
    st_dash.set_page_config(page_title="Omnisapient Simulation Dashboard", layout="wide")

    if not STREAMLIT_AVAILABLE:
        st_dash.error(
            "Streamlit is not installed. Please install it (`pip install streamlit`) to view the dashboard."
        )
        return

    # Ensure language is always initialized
    if "lang" not in st_dash.session_state:
        st_dash.session_state.lang = "en"

    # Ensure LOCALES is in session_state, primarily for plugins to extend it
    if "LOCALES" not in st_dash.session_state:
        st_dash.session_state.LOCALES = LOCALES

    # Reload plugins to ensure any newly generated ones or updated locales are picked up
    # This should be safe with st.cache_resource on load_plugin_dashboard_panels_cached()
    # but for dynamic runtime additions, a explicit re-load might be desired.
    # For now, rely on cache_resource and reruns.

    authenticate_user()  # This function handles login and stops execution if not authenticated
    user_role = st_dash.session_state.user_role
    user = st_dash.session_state.user

    st_dash.title(f"🔮 Omnisapient AI Simulation Analytics - {t('welcome_message')}, {user}")

    with st_dash.sidebar:
        st_dash.header(t("settings_header"))  # Use translated header
        st_dash.info(f"{t('current_role')}: **{user_role}**")  # Example of new translation key
        st_dash.info(f"{t('user_label')}: **{user}**")  # Example of new translation key

        # Language selector
        st_dash.session_state.lang = st_dash.selectbox(
            t("language_selector_label"),
            options=list(LOCALES.keys()),
            format_func=lambda x: {"en": "English", "es": "Español"}[x],  # Display names
            key="language_selector",
            help=t("language_selector_tooltip"),
        )

        # High Contrast Mode (Conceptual)
        high_contrast_mode = st_dash.checkbox(
            t("high_contrast_mode_label"),
            key="high_contrast_checkbox",
            help=t("high_contrast_mode_tooltip"),
        )
        if high_contrast_mode:
            st_dash.markdown(
                """
                <style>
                body {
                    filter: contrast(150%) brightness(120%);
                    background-color: black !important;
                    color: white !important;
                }
                .stButton > button {
                    border: 2px solid white !important;
                    color: white !important;
                    background-color: black !important;
                }
                .st-bg { background-color: black !important; }
                .st-d { color: white !important; }
                /* Add more specific rules for text, links, inputs etc. */
                </style>
                """,
                unsafe_allow_html=True,
            )
            # This is a very basic conceptual high contrast. For real accessibility,
            # much more comprehensive CSS styling is needed, ideally loaded from a .css file.

        # Dark Mode Toggle (Conceptual - Streamlit has built-in dark mode)
        dark_mode = st_dash.checkbox(
            t("dark_mode_label"), key="dark_mode_checkbox", help=t("dark_mode_tooltip")
        )
        if dark_mode:
            # Streamlit often handles dark mode automatically based on browser/OS settings
            # This is more for a custom toggle if Streamlit's native mode is overridden.
            pass  # Implement custom dark mode CSS if needed, or rely on Streamlit's theme.

        st_dash.markdown("---")  # Separator in sidebar

        # Render plugin sidebar components
        sidebar_components = [
            c
            for c in get_registered_sidebar_components()
            if not c["roles"] or user_role in c["roles"] or user_role == "admin"
        ]
        for scomp in sidebar_components:
            with st_dash.expander(
                scomp["title"],
                help=scomp["description"] if scomp["description"] else None,
            ):
                try:
                    scomp["func"](st_dash)  # Pass st_dash directly
                except Exception as e:
                    # Exception caught by decorator, but this outer catch is for robustness
                    st_dash.error(f"Error rendering sidebar for {scomp['plugin']}: {e}")
                    logging.error(
                        f"Error rendering sidebar for {scomp['plugin']}: {e}",
                        exc_info=True,
                    )

    # Initialize PluginManager instance and store in session state
    if PLUGIN_MANAGER_AVAILABLE and "plugin_manager_instance" not in st_dash.session_state:
        st_dash.session_state.plugin_manager_instance = PluginManager(
            plugins_dir=Config.PLUGINS_DIR
        )
        st_dash.session_state.plugin_manager_instance.load_all()
        logging.info("PluginManager initialized and plugins loaded.")
    elif not PLUGIN_MANAGER_AVAILABLE:
        st_dash.warning("PluginManager could not be loaded. Plugin Gallery will be unavailable.")

    # Create tabs for navigation, including dynamic plugin main components
    main_components = [
        c
        for c in get_registered_main_components()
        if not c["roles"] or user_role in c["roles"] or user_role == "admin"
    ]
    tab_names = [
        t("simulation_results_tab"),  # Translated
        t("plugin_gallery_tab"),  # Translated
        t("onboarding_wizard_tab"),  # Translated
    ] + [f"🧩 {c['title']}" for c in main_components]
    tabs = st_dash.tabs(tab_names)

    with tabs[0]:
        results = load_all_simulation_results()
        if not results:
            st_dash.info(t("no_simulation_results_found"))  # Translated
        else:
            st_dash.header(t("recent_simulation_runs_header"))  # Translated

            # Search box for filtering results
            search_query = st_dash.text_input(
                t("search_results_label"), "", help=t("search_results_tooltip")
            )

            session_names = sorted(
                list(
                    set(
                        [
                            os.path.basename(r.get("test_file", f"unknown_session_{idx}")).split(
                                "_tests"
                            )[0]
                            for idx, r in enumerate(results)
                        ]
                        if isinstance(results, list)
                        else []
                    )
                )
            )
            selected_session = st_dash.sidebar.selectbox(
                t("select_session_label"),
                ["All"] + session_names,
                key="session_selector",
                help=t("select_session_tooltip"),
            )  # Translated

            filtered_results_by_session = []
            if selected_session == "All":
                filtered_results_by_session = results
            else:
                filtered_results_by_session = [
                    r
                    for r in results
                    if os.path.basename(r.get("test_file", "")).startswith(selected_session)
                ]

            # Apply search filter
            if search_query:
                filtered_results = [
                    r
                    for r in filtered_results_by_session
                    if search_query.lower()
                    in json.dumps(r).lower()  # Simple string search across JSON content
                ]
                if not filtered_results:
                    st_dash.warning(t("no_results_for_search_query_warning"))
            else:
                filtered_results = filtered_results_by_session

            if not filtered_results:
                if not search_query:  # Only show this warning if no search query was entered
                    st_dash.warning(t("no_results_for_selected_session_warning"))
            else:
                run_options = []
                for r in filtered_results:
                    filename_base = os.path.basename(r.get("_filepath", "no_file_path.json"))
                    try:
                        mtime = datetime.fromtimestamp(os.path.getmtime(r["_filepath"])).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    except (KeyError, FileNotFoundError):
                        mtime = "N/A"
                    run_options.append(f"{filename_base} (Generated: {mtime})")

                # Ensure selected_run_index is valid for filtered_results
                if (
                    "selected_run_index" not in st_dash.session_state
                    or st_dash.session_state.selected_run_index >= len(filtered_results)
                    or st_dash.session_state.selected_run_index >= len(run_options)
                ):  # defensive check
                    st_dash.session_state.selected_run_index = 0

                selected_run_index = st_dash.selectbox(
                    t("select_run_label"),  # Translated
                    range(len(filtered_results)),
                    index=st_dash.session_state.selected_run_index,
                    format_func=lambda x: run_options[x],
                    key="run_selector",
                    help=t("select_run_tooltip"),
                )
                st_dash.session_state.selected_run_index = selected_run_index

                selected_result = filtered_results[selected_run_index] if filtered_results else None

                live_job_id = selected_result.get("job_id") if selected_result else None

                # Live data update checkbox with autorefresh
                st_dash.sidebar.markdown("---")
                if REDIS_AVAILABLE and live_job_id:
                    enable_live_data = st_dash.sidebar.checkbox(
                        t("enable_live_data_updates_label"),
                        key="live_data_checkbox",
                        help=t("enable_live_data_tooltip"),
                    )
                    if enable_live_data:
                        live_update_interval = st_dash.sidebar.slider(
                            t("live_update_interval_label"),
                            1,
                            10,
                            3,
                            key="live_data_interval_slider",
                            help=t("live_update_interval_tooltip"),
                        )
                        st_autorefresh(
                            interval=live_update_interval * 1000,
                            key="live_data_refresh",
                        )
                        st_dash.info(t("live_data_enabled_info"))
                    else:
                        st_dash.warning(t("live_updates_paused_warning"))
                else:
                    st_dash.info(t("live_data_not_available_info"))  # New translation key

                live_data_placeholder = st_dash.empty()

                if selected_result:
                    with live_data_placeholder.container():
                        # Update selected_result with live data if enabled
                        if (
                            REDIS_AVAILABLE
                            and live_job_id
                            and st_dash.session_state.get("live_data_checkbox", False)
                        ):
                            live_data = get_live_data(live_job_id)
                            if live_data:
                                selected_result.update(live_data)

                        display_core_metrics(selected_result)
                        st_dash.header(t("self_fixing_engineer_visualization_header"))  # Translated
                        if WORKFLOW_VIZ_AVAILABLE:
                            if st_dash.checkbox(
                                t("show_workflow_flowchart_label"),
                                value=True,
                                key="show_workflow_flowchart",
                                help=t("show_workflow_flowchart_tooltip"),
                            ):  # Translated
                                render_workflow_viz(
                                    selected_result,
                                    prefer_plotly=True,
                                    summary_callback=_display_summary_and_details,
                                )
                            else:
                                st_dash.info(t("enable_workflow_flowchart_info"))  # Translated
                        else:
                            st_dash.info(t("workflow_viz_unavailable_info"))  # Translated

                        st_dash.markdown("---")
                        st_dash.subheader(t("run_details_subheader"))  # Translated
                        for i, run in enumerate(selected_result.get("runs", [])):
                            if isinstance(run, dict):
                                with st_dash.expander(
                                    f"Run {run.get('run', i)} - {'PASS' if run.get('returncode') == 0 else 'FAIL'}",
                                    key=f"run_expander_{run.get('run', i)}_{i}",
                                    help=t("run_details_expander_tooltip"),
                                ):
                                    st_dash.json(run)

                        st_dash.subheader(t("visualizations_subheader"))  # Translated
                        if PLOTLY_AVAILABLE:
                            runs = selected_result.get("runs", [])
                            if runs:
                                df_data = {
                                    "Run": [r.get("run") for r in runs if isinstance(r, dict)],
                                    "CPU avg (%)": [
                                        r.get("metrics", {}).get("cpu_percent_avg", 0)
                                        for r in runs
                                        if isinstance(r, dict) and "metrics" in r
                                    ],
                                    "Memory max (MB)": [
                                        r.get("metrics", {}).get("memory_rss_max_mb", 0)
                                        for r in runs
                                        if isinstance(r, dict) and "metrics" in r
                                    ],
                                    "Duration (s)": [
                                        r.get("metrics", {}).get("duration_seconds", 0)
                                        for r in runs
                                        if isinstance(r, dict) and "metrics" in r
                                    ],
                                    "Return Code": [
                                        r.get("returncode") for r in runs if isinstance(r, dict)
                                    ],
                                }
                                # Create basic Plotly graph objects figure
                                fig = go.Figure(
                                    data=[
                                        go.Bar(
                                            name="CPU avg (%)",
                                            x=df_data["Run"],
                                            y=df_data["CPU avg (%)"],
                                        ),
                                        go.Bar(
                                            name="Memory max (MB)",
                                            x=df_data["Run"],
                                            y=df_data["Memory max (MB)"],
                                        ),
                                        go.Bar(
                                            name="Duration (s)",
                                            x=df_data["Run"],
                                            y=df_data["Duration (s)"],
                                        ),
                                    ]
                                )
                                fig.update_layout(
                                    barmode="group", title_text="Resource Usage per Run"
                                )
                                st_dash.plotly_chart(fig, use_container_width=True)
                            else:
                                st_dash.info(t("no_run_data_for_plotly_info"))  # Translated
                        else:
                            # Fallback to Streamlit's built-in charts
                            runs = selected_result.get("runs", [])
                            if all(isinstance(r, dict) and "metrics" in r for r in runs):
                                st_dash.bar_chart(
                                    {
                                        "CPU Usage (%)": [
                                            r["metrics"].get("cpu_percent_avg", 0) for r in runs
                                        ],
                                        "Max Memory (MB)": [
                                            r["metrics"].get("memory_rss_max_mb", 0) for r in runs
                                        ],
                                    }
                                )
                            else:
                                st_dash.warning(
                                    t("invalid_run_data_warning")
                                )  # New translation key
                            st_dash.info(t("install_plotly_info"))  # Translated

                        if "flaky_plot" in selected_result and os.path.exists(
                            selected_result["flaky_plot"]
                        ):
                            st_dash.subheader(t("test_flakiness_trend_subheader"))  # Translated
                            st_dash.image(selected_result["flaky_plot"])

                        if selected_result.get("agentic"):
                            st_dash.subheader(t("agentic_swarm_analysis_subheader"))  # Translated
                            for agent_key in [
                                "planner",
                                "fault",
                                "analyzer",
                                "healer",
                                "ethics",
                                "sustainability",
                            ]:
                                if agent_key in selected_result:
                                    st_dash.json(
                                        {agent_key.title(): selected_result[agent_key]},
                                        expanded=False,
                                    )
                            if selected_result.get("consensus"):
                                st_dash.json(
                                    {
                                        "Consensus Protocol": {
                                            "decision": selected_result["consensus"]
                                        }
                                    },
                                    expanded=False,
                                )
                            if selected_result.get("healer", {}).get("llm_suggestion"):
                                st_dash.markdown(
                                    f"**{t('ai_healer_suggestion_label')}**\n{selected_result['healer']['llm_suggestion']}"
                                )  # Translated

                        st_dash.subheader(t("custom_plugin_panels_subheader"))  # Translated
                        available_panels = get_registered_dashboard_panels()
                        if not available_panels:
                            st_dash.info(t("no_custom_plugin_panels_registered_info"))  # Translated
                        for panel_info in available_panels:
                            if (
                                panel_info["roles"]
                                and user_role not in panel_info["roles"]
                                and user_role != "admin"
                            ):
                                st_dash.info(
                                    f"{t('panel_not_available_for_role_info')} ('{panel_info['title']}') ({user_role})."
                                )  # Translated
                                continue
                            with st_dash.expander(
                                f"⚙️ {panel_info['title']}",
                                expanded=False,
                                help=(
                                    panel_info["description"] if panel_info["description"] else None
                                ),
                            ):
                                if panel_info["description"]:
                                    st_dash.markdown(f"*{panel_info['description']}*")
                                try:
                                    # The decorator `plugin_callback_handler` is already applied during registration
                                    panel_info["render_function"](st_dash, selected_result)
                                except Exception as e:
                                    # This outer catch is for extreme robustness, but the decorator should handle most
                                    st_dash.error(
                                        f"Error rendering plugin panel '{panel_info['title']}': {e}"
                                    )
                                    st_dash.exception(e)

                        st_dash.markdown("---")
                        st_dash.subheader(t("raw_simulation_result_data_subheader"))  # Translated
                        st_dash.json(selected_result, expanded=False)

                        st_dash.subheader(t("custom_report_generation_subheader"))  # Translated
                        with st_dash.form("custom_report_form"):
                            report_metrics = st_dash.multiselect(
                                t("select_metrics_for_report_label"),  # Translated
                                options=[
                                    t("total_runs"),
                                    t("failed_runs"),
                                    t("code_coverage"),
                                    t("rl_reward"),
                                    t("ethical_compliance"),
                                    t("energy_consumed"),
                                    t("carbon_emitted"),
                                ],  # Translated metrics
                                default=[
                                    t("total_runs"),
                                    t("failed_runs"),
                                    t("code_coverage"),
                                ],  # Default translated metrics
                                key="report_metrics_multiselect",
                                help=t("select_metrics_tooltip"),
                            )
                            time_range = st_dash.slider(
                                t("select_time_range_label"),  # Translated
                                min_value=1,
                                max_value=90,
                                value=30,
                                key="report_time_range_slider",
                                help=t("select_time_range_tooltip"),
                            )
                            viz_type = st_dash.selectbox(
                                t("select_visualization_type_label"),  # Translated
                                options=[
                                    t("bar_chart"),
                                    t("line_chart"),
                                    t("table"),
                                ],  # Translated viz types
                                key="report_viz_type_selectbox",
                                help=t("select_visualization_type_tooltip"),
                            )
                            submitted = st_dash.form_submit_button(
                                t("generate_report_button"),
                                key="generate_report_button",
                                help=t("generate_report_button_tooltip"),
                            )  # Translated
                        if submitted:
                            st_dash.success(
                                f"{t('generated_report_success_message')} {viz_type} {t('for_metrics')} {', '.join(report_metrics)} {t('over_last')} {time_range} {t('days')}."
                            )  # Translated message components
                            st_dash.info(t("actual_report_generation_info"))  # Translated

                        st_dash.subheader(t("export_options_subheader"))  # Translated
                        col_export1, col_export2, col_export3 = st_dash.columns(3)
                        json_export_str = json.dumps(selected_result, indent=2)
                        json_filename = (
                            f"simulation_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                        )
                        col_export1.download_button(
                            label=t("download_raw_json_button"),  # Translated
                            data=json_export_str,
                            file_name=json_filename,
                            mime="application/json",
                            key="download_json_btn",
                            help=t("download_json_tooltip"),
                        )
                        if col_export1.button(
                            t("copy_json_to_clipboard_button"),
                            key="copy_json_btn",
                            help=t("copy_json_tooltip"),
                        ):  # Translated
                            st_dash.code(json_export_str, language="json")
                            st_dash.success(
                                t("json_copied_to_clipboard_info"), icon="📋"
                            )  # Translated

                        html_report_content = (
                            "<!DOCTYPE html>\n"
                            "<html>\n"
                            "<head>\n"
                            "    <title>Simulation Report</title>\n"
                            "    <style>\n"
                            "        body { font-family: sans-serif; margin: 20px; }\n"
                            "        pre { background-color: #f0f0f0; padding: 10px; border-radius: 5px; white-space: pre-wrap; }\n"
                            "        h1, h2, h3 { color: #333; }\n"
                            "    </style>\n"
                            "</head>\n"
                            "<body>\n"
                            f"    <h1>{t('omnisapient_ai_simulation_report_header')}</h1>\n"  # Translated
                            f"    <h2>{t('run_details_header')}: {os.path.basename(selected_result.get('test_file', 'Unnamed'))}</h2>\n"  # Translated
                            f"    <h3>{t('core_metrics_header')}</h3>\n"  # Translated
                            f"    <p>{t('total_runs')}: {len(selected_result.get('runs', []))}</p>\n"  # Translated
                            f"    <p>{t('failed_runs')}: {sum(1 for r in selected_result.get('runs', []) if isinstance(r, dict) and r.get('returncode') != 0)}</p>\n"  # Translated
                            f"    <p>{t('code_coverage')}: {selected_result.get('coverage', {}).get('coverage', 0)*100:.1f}%</p>\n"  # Translated
                            f"    <p>{t('rl_reward')}: {selected_result.get('rl_reward', 'N/A')}</p>\n"  # Translated
                            f"    <p>{t('ethical_compliance')}: {'Yes' if selected_result.get('ethics', {}).get('compliant', False) else 'No'}</p>\n"  # Translated
                            f"    <p>{t('energy_consumed')}: {selected_result.get('sustainability', {}).get('energy_kwh', 'N/A')} kWh</p>\n"  # Translated
                            f"    <h3>{t('raw_data_header')}</h3>\n"  # Translated
                            f"    <pre>{json.dumps(selected_result, indent=2)}</pre>\n"
                            "</body>\n"
                            "</html>"
                        )
                        html_filename = (
                            f"simulation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
                        )
                        col_export2.download_button(
                            label=t("download_html_report_button"),  # Translated
                            data=html_report_content,
                            file_name=html_filename,
                            mime="text/html",
                            key="download_html_btn",
                            help=t("download_html_tooltip"),
                        )
                        csv_content = "Run,ReturnCode,DurationSeconds,AvgCPUPercent,MaxMemoryMB\n"
                        for run in selected_result.get("runs", []):
                            metrics = run.get("metrics", {})
                            # Harden CSV formatting to handle non-numeric values
                            run_val = run.get("run", "N/A")
                            returncode_val = run.get("returncode", "N/A")
                            duration_val = (
                                f"{metrics.get('duration_seconds', 'N/A'):.2f}"
                                if isinstance(metrics.get("duration_seconds"), (int, float))
                                else "N/A"
                            )
                            cpu_val = (
                                f"{metrics.get('cpu_percent_avg', 'N/A'):.2f}"
                                if isinstance(metrics.get("cpu_percent_avg"), (int, float))
                                else "N/A"
                            )
                            mem_val = (
                                f"{metrics.get('memory_rss_max_mb', 'N/A'):.2f}"
                                if isinstance(metrics.get("memory_rss_max_mb"), (int, float))
                                else "N/A"
                            )
                            csv_content += (
                                f"{run_val},"
                                f"{returncode_val},"
                                f"{duration_val},"
                                f"{cpu_val},"
                                f"{mem_val}\n"
                            )
                        csv_filename = (
                            f"simulation_metrics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                        )
                        col_export3.download_button(
                            label=t("download_run_metrics_csv_button"),  # Translated
                            data=csv_content,
                            file_name=csv_filename,
                            mime="text/csv",
                            key="download_csv_btn",
                            help=t("download_csv_tooltip"),
                        )
    with tabs[1]:
        if PLUGIN_MANAGER_AVAILABLE:
            # Pass the initialized plugin_manager_instance from session state
            if "plugin_manager_instance" in st_dash.session_state:
                display_plugin_gallery(st_dash.session_state.plugin_manager_instance, user_role)
            else:
                st_dash.warning(t("plugin_manager_not_initialized_warning"))  # Translated
        else:
            st_dash.error(t("plugin_gallery_unavailable_error"))  # Translated

    with tabs[2]:
        display_onboarding_wizard()

    # Render dynamic plugin main components
    base_tab_index = 3
    for comp in main_components:
        with tabs[base_tab_index]:
            if comp["description"]:
                st_dash.markdown(f"*{comp['description']}*")
            try:
                # The decorator `plugin_callback_handler` is already applied during registration
                comp["func"](st_dash)
            except Exception as e:
                # This outer catch is for extreme robustness, but the decorator should handle most
                st_dash.error(f"Error rendering main component for {comp['plugin']}: {e}")
                st_dash.exception(e)
        base_tab_index += 1


# --- Localisation additions ---
# Add new keys to LOCALES for all translated strings
LOCALES["en"].update(
    {
        "current_role": "Current Role",
        "user_label": "User",
        "simulation_results_tab": "📊 Simulation Results",
        "plugin_gallery_tab": "🔌 Plugin Gallery",
        "onboarding_wizard_tab": "🚀 Onboarding Wizard",
        "no_simulation_results_found": "No simulation results found. Run some simulations first!",
        "recent_simulation_runs_header": "Recent Simulation Runs",
        "select_session_label": "Select Session:",
        "no_results_for_selected_session_warning": "No results found for the selected session.",
        "select_run_label": "Select a specific run for detailed view:",
        "enable_live_data_updates_label": "Enable Live Data Updates",
        "pause_live_updates_button": "Pause Live Updates",
        "enable_live_updates_button": "Enable Live Updates",  # New key
        "live_update_interval_label": "Live Update Interval (seconds)",
        "live_data_enabled_info": "Live data updates enabled. Dashboard will auto-refresh.",
        "live_updates_paused_warning": "Live updates are currently paused.",  # New key
        "live_data_not_available_info": "Live data updates are not available for the selected run or Redis is not connected.",  # New key
        "self_fixing_engineer_visualization_header": "Self-Fixing Engineer Visualization",
        "show_workflow_flowchart_label": "Show Workflow Flowchart",
        "enable_workflow_flowchart_info": "Enable the checkbox above to view the detailed workflow visualization.",
        "workflow_viz_unavailable_info": "Workflow visualization feature is not available. Please ensure `workflow_viz.py` is present and/or install Plotly.",
        "run_details_subheader": "Run Details",
        "visualizations_subheader": "Visualizations",
        "no_run_data_for_plotly_info": "No run data available for Plotly visualization.",
        "install_plotly_info": "Install Plotly for advanced charts: `pip install plotly`.",
        "test_flakiness_trend_subheader": "Test Flakiness Trend",
        "agentic_swarm_analysis_subheader": "Agentic Swarm Analysis",
        "ai_healer_suggestion_label": "AI Healer Suggestion:",
        "custom_plugin_panels_subheader": "Custom Plugin Panels",
        "no_custom_plugin_panels_registered_info": "No custom plugin panels are registered.",
        "panel_not_available_for_role_info": "Panel is not available for your current role",  # Simplified for generic use
        "raw_simulation_result_data_subheader": "Raw Simulation Result Data",
        "custom_report_generation_subheader": "Custom Report Generation",
        "select_metrics_for_report_label": "Select metrics for your report:",
        "total_runs": "Total Runs",
        "failed_runs": "Failed Runs",
        "code_coverage": "Code Coverage",
        "rl_reward": "RL Reward",
        "ethical_compliance": "Ethical Compliance",
        "energy_consumed": "Energy Consumed",
        "carbon_emitted": "Carbon Emitted",
        "select_time_range_label": "Select time range (days):",
        "select_visualization_type_label": "Select visualization type:",
        "bar_chart": "Bar Chart",
        "line_chart": "Line Chart",
        "table": "Table",
        "generate_report_button": "Generate Report",
        "generated_report_success_message": "Generated",
        "for_metrics": "for metrics:",
        "over_last": "over last",
        "days": "days",
        "actual_report_generation_info": "Actual report generation logic needs to be implemented here.",
        "export_options_subheader": "Export Options",
        "download_raw_json_button": "Download Raw JSON",
        "copy_json_to_clipboard_button": "Copy JSON to Clipboard",
        "json_copied_to_clipboard_info": "JSON content displayed above. Please copy manually for now.",
        "omnisapient_ai_simulation_report_header": "Omnisapient AI Simulation Report",
        "run_details_header": "Run Details",
        "core_metrics_header": "Core Metrics",
        "raw_data_header": "Raw Data",
        "download_html_report_button": "Download HTML Report",
        "download_run_metrics_csv_button": "Download Run Metrics (CSV)",
        "plugin_manager_not_initialized_warning": "PluginManager not yet initialized. Please refresh if this persists.",
        "plugin_gallery_unavailable_error": "Plugin Gallery is unavailable because PluginManager could not be loaded.",
        "enable_plugin_tooltip": "Enable this plugin to make its functionalities available.",
        "disable_plugin_tooltip": "Disable this plugin. It will no longer be active.",
        "admin_role_required_info": "Admin role required to enable/disable plugins.",
        "reload_plugin_tooltip": "Reload this plugin. Useful for Python plugins after code changes.",
        "reload_python_plugin_info": "Reload not applicable for this plugin type.",
        "reload_not_applicable_info": "Reload not applicable for this plugin type.",
        "run_health_check_tooltip": "Run a health check for this plugin to verify its operational status.",
        "reset_plugin_tooltip": "Attempt to reset the plugin, e.g., clear its state or re-initialize.",
        "project_type_tooltip": "Select the primary type of your AI project.",
        "plugin_types_tooltip": "Choose the programming languages or frameworks for your plugins.",
        "pubsub_backend_tooltip": "Select the messaging backend for real-time communication.",
        "redis_url_tooltip": "Enter the URL for your Redis server.",
        "nats_url_tooltip": "Enter the URL for your NATS server.",
        "kafka_url_tooltip": "Enter the bootstrap servers for your Kafka cluster.",
        "rabbitmq_url_tooltip": "Enter the AMQP URL for your RabbitMQ server.",
        "gcs_bucket_tooltip": "Enter the Google Cloud Storage bucket name for events.",
        "azure_conn_str_tooltip": "Enter the Azure Storage Account connection string.",
        "azure_container_tooltip": "Enter the Azure Blob Storage container name for events.",
        "etcd_host_tooltip": "Enter the etcd host address.",
        "etcd_port_tooltip": "Enter the etcd port.",
        "checkpoint_backend_tooltip": "Select the backend for persisting checkpoint data.",
        "fs_dir_tooltip": "Enter the local directory path for file system checkpoints.",
        "s3_bucket_tooltip": "Enter the Amazon S3 bucket name for checkpoints.",
        "chk_redis_url_tooltip": "Enter the Redis URL for checkpoint storage.",
        "pg_dsn_tooltip": "Enter the PostgreSQL Data Source Name.",
        "chk_gcs_bucket_tooltip": "Enter the Google Cloud Storage bucket name for checkpoints.",
        "chk_azure_conn_str_tooltip": "Enter the Azure Storage Account connection string for checkpoints.",
        "chk_azure_container_tooltip": "Enter the Azure Blob Storage container name for checkpoints.",
        "chk_etcd_host_tooltip": "Enter the etcd host address for checkpoints.",
        "chk_etcd_port_tooltip": "Enter the etcd port for checkpoints.",
        "generate_config_tooltip": "Generate the configuration file based on your selections.",
        "run_all_health_checks_tooltip": "Run health checks for all configured backends and loaded plugins.",
        "run_demo_job_tooltip": "Display a command to run a conceptual demo simulation job.",
        "download_config_tooltip": "Download the generated configuration file.",
        "search_results_label": "Search Simulation Results:",
        "search_results_tooltip": "Filter simulation results by keywords in their content.",
        "no_results_for_search_query_warning": "No results found matching your search query.",
        "select_session_tooltip": "Filter results by simulation session.",
        "select_run_tooltip": "Choose a specific simulation run for detailed analysis.",
        "enable_live_data_tooltip": "Toggle real-time updates for the selected simulation run.",
        "live_update_interval_tooltip": "Adjust how frequently live data is refreshed.",
        "show_workflow_flowchart_tooltip": "Display a visual flowchart of the self-fixing engineer's workflow.",
        "run_details_expander_tooltip": "Click to expand and view detailed logs and metrics for this run.",
        "select_metrics_tooltip": "Choose which performance metrics to include in your custom report.",
        "select_time_range_tooltip": "Set the historical time frame (in days) for the report.",
        "select_visualization_type_tooltip": "Pick the chart type for your custom report.",
        "generate_report_button_tooltip": "Generate a custom report based on your selections (conceptual).",
        "download_json_tooltip": "Download the raw JSON data for the selected simulation result.",
        "copy_json_tooltip": "Copy the raw JSON data to your clipboard.",
        "download_html_tooltip": "Generate and download a basic HTML report.",
        "download_csv_tooltip": "Download a CSV file containing key metrics for all runs in the selected result.",
        "invalid_run_data_warning": "Run data is missing or malformed, cannot generate chart.",
    }
)

LOCALES["es"].update(
    {
        "current_role": "Rol actual",
        "user_label": "Usuario",
        "simulation_results_tab": "📊 Resultados de simulación",
        "plugin_gallery_tab": "🔌 Galería de plugins",
        "onboarding_wizard_tab": "🚀 Asistente de incorporación",
        "no_simulation_results_found": "No se encontraron resultados de simulación. ¡Ejecute algunas simulaciones primero!",
        "recent_simulation_runs_header": "Ejecuciones de simulación recientes",
        "select_session_label": "Seleccionar sesión:",
        "no_results_for_selected_session_warning": "No se encontraron resultados para la sesión seleccionada.",
        "select_run_label": "Seleccionar una ejecución específica para ver en detalle:",
        "enable_live_data_updates_label": "Habilitar actualizaciones de datos en vivo",
        "pause_live_updates_button": "Pausar actualizaciones en vivo",
        "enable_live_updates_button": "Habilitar actualizaciones en vivo",
        "live_update_interval_label": "Intervalo de actualización en vivo (segundos)",
        "live_data_enabled_info": "Actualizaciones de datos en vivo habilitadas. El panel se actualizará automáticamente.",
        "live_updates_paused_warning": "Las actualizaciones en vivo están actualmente pausadas.",
        "live_data_not_available_info": "Las actualizaciones en vivo no están disponibles para la ejecución seleccionada o Redis no está conectado.",
        "self_fixing_engineer_visualization_header": "Visualización del ingeniero de autorreparación",
        "show_workflow_flowchart_label": "Mostrar diagrama de flujo de trabajo",
        "enable_workflow_flowchart_info": "Habilite la casilla de verificación anterior para ver la visualización detallada del flujo de trabajo.",
        "workflow_viz_unavailable_info": "La función de visualización de flujo de trabajo no está disponible. Asegúrese de que `workflow_viz.py` esté presente y/o instale Plotly.",
        "run_details_subheader": "Detalles de la ejecución",
        "visualizations_subheader": "Visualizaciones",
        "no_run_data_for_plotly_info": "No hay datos de ejecución disponibles para la visualización de Plotly.",
        "install_plotly_info": "Instale Plotly para gráficos avanzados: `pip install plotly`.",
        "test_flakiness_trend_subheader": "Tendencia de inestabilidad de las pruebas",
        "agentic_swarm_analysis_subheader": "Análisis de enjambre agéntico",
        "ai_healer_suggestion_label": "Sugerencia del sanador de IA:",
        "custom_plugin_panels_subheader": "Paneles de plugins personalizados",
        "no_custom_plugin_panels_registered_info": "No hay paneles de plugins personalizados registrados.",
        "panel_not_available_for_role_info": "El panel no está disponible para su rol actual",
        "raw_simulation_result_data_subheader": "Datos de resultados de simulación en bruto",
        "custom_report_generation_subheader": "Generación de informes personalizados",
        "select_metrics_for_report_label": "Seleccione métricas para su informe:",
        "total_runs": "Ejecuciones totales",
        "failed_runs": "Ejecuciones fallidas",
        "code_coverage": "Cobertura de código",
        "rl_reward": "Recompensa de RL",
        "ethical_compliance": "Cumplimiento ético",
        "energy_consumed": "Energía consumida",
        "carbon_emitted": "Carbono emitido",
        "select_time_range_label": "Seleccionar rango de tiempo (días):",
        "select_visualization_type_label": "Seleccionar tipo de visualización:",
        "bar_chart": "Gráfico de barras",
        "line_chart": "Gráfico de líneas",
        "table": "Tabla",
        "generate_report_button": "Generar informe",
        "generated_report_success_message": "Generado",
        "for_metrics": "para métricas:",
        "over_last": "durante los últimos",
        "days": "días",
        "actual_report_generation_info": "La lógica de generación de informes real debe implementarse aquí.",
        "export_options_subheader": "Opciones de exportación",
        "download_raw_json_button": "Descargar JSON sin formato",
        "copy_json_to_clipboard_button": "Copiar JSON al portapapeles",
        "json_copied_to_clipboard_info": "Contenido JSON mostrado arriba. Cópielo manualmente por ahora.",
        "omnisapient_ai_simulation_report_header": "Informe de simulación de IA de Omnisapient",
        "run_details_header": "Detalles de la ejecución",
        "core_metrics_header": "Métricas principales",
        "raw_data_header": "Datos sin formato",
        "download_html_report_button": "Descargar informe HTML",
        "download_run_metrics_csv_button": "Descargar métricas de ejecución (CSV)",
        "plugin_manager_not_initialized_warning": "PluginManager aún no inicializado. Actualice si esto persiste.",
        "plugin_gallery_unavailable_error": "La Galería de plugins no está disponible porque no se pudo cargar PluginManager.",
        "enable_plugin_tooltip": "Habilitar este plugin para que sus funcionalidades estén disponibles.",
        "disable_plugin_tooltip": "Deshabilitar este plugin. Ya no estará activo.",
        "admin_role_required_info": "Se requiere rol de administrador para habilitar/deshabilitar plugins.",
        "reload_plugin_tooltip": "Recargar este plugin. Útil para plugins de Python después de cambios en el código.",
        "reload_python_plugin_info": "Recargar no aplicable para este tipo de plugin.",
        "reload_not_applicable_info": "Recargar no aplicable para este tipo de plugin.",
        "run_health_check_tooltip": "Ejecutar una verificación de estado para este plugin para verificar su estado operativo.",
        "reset_plugin_tooltip": "Intentar restablecer el plugin, por ejemplo, borrar su estado o reinicializar.",
        "project_type_tooltip": "Seleccione el tipo principal de su proyecto de IA.",
        "plugin_types_tooltip": "Elija los lenguajes de programación o frameworks para sus plugins.",
        "pubsub_backend_tooltip": "Seleccione el backend de mensajería para la comunicación en tiempo real.",
        "redis_url_tooltip": "Introduzca la URL de su servidor Redis.",
        "nats_url_tooltip": "Introduzca la URL de su servidor NATS.",
        "kafka_url_tooltip": "Introduzca los servidores de arranque de su clúster Kafka.",
        "rabbitmq_url_tooltip": "Introduzca la URL AMQP de su servidor RabbitMQ.",
        "gcs_bucket_tooltip": "Introduzca el nombre del bucket de Google Cloud Storage para eventos.",
        "azure_conn_str_tooltip": "Introduzca la cadena de conexión de su cuenta de Azure Storage.",
        "azure_container_tooltip": "Introduzca el nombre del contenedor de Azure Blob Storage para eventos.",
        "etcd_host_tooltip": "Introduzca la dirección del host de etcd.",
        "etcd_port_tooltip": "Introduzca el puerto de etcd.",
        "checkpoint_backend_tooltip": "Seleccione el backend para persistir los datos de los puntos de control.",
        "fs_dir_tooltip": "Introduzca la ruta del directorio local para los puntos de control del sistema de archivos.",
        "s3_bucket_tooltip": "Introduzca el nombre del bucket de Amazon S3 para los puntos de control.",
        "chk_redis_url_tooltip": "Introduzca la URL de Redis para el almacenamiento de puntos de control.",
        "pg_dsn_tooltip": "Introduzca el DSN de PostgreSQL.",
        "chk_gcs_bucket_tooltip": "Introduzca el nombre del bucket de Google Cloud Storage para los puntos de control.",
        "chk_azure_conn_str_tooltip": "Introduzca la cadena de conexión de su cuenta de Azure Storage para los puntos de control.",
        "chk_azure_container_tooltip": "Introduzca el nombre del contenedor de Azure Blob Storage para los puntos de control.",
        "chk_etcd_host_tooltip": "Introduzca la dirección del host de etcd para los puntos de control.",
        "chk_etcd_port_tooltip": "Introduzca el puerto de etcd para los puntos de control.",
        "generate_config_tooltip": "Generar el archivo de configuración basado en sus selecciones.",
        "run_all_health_checks_tooltip": "Ejecutar verificaciones de estado para todos los backends configurados y plugins cargados.",
        "run_demo_job_tooltip": "Mostrar un comando para ejecutar un trabajo de simulación de demostración conceptual.",
        "download_config_tooltip": "Descargar el archivo de configuración generado.",
        "search_results_label": "Buscar resultados de simulación:",
        "search_results_tooltip": "Filtrar resultados de simulación por palabras clave en su contenido.",
        "no_results_for_search_query_warning": "No se encontraron resultados que coincidan con su consulta de búsqueda.",
        "select_session_tooltip": "Filtrar resultados por sesión de simulación.",
        "select_run_tooltip": "Elija una ejecución de simulación específica para un análisis detallado.",
        "enable_live_data_tooltip": "Activar/desactivar actualizaciones en tiempo real para la ejecución de simulación seleccionada.",
        "live_update_interval_tooltip": "Ajustar la frecuencia con la que se actualizan los datos en vivo.",
        "show_workflow_flowchart_tooltip": "Mostrar un diagrama de flujo visual del flujo de trabajo del ingeniero de autorreparación.",
        "run_details_expander_tooltip": "Haga clic para expandir y ver los registros detallados y las métricas de esta ejecución.",
        "select_metrics_tooltip": "Elija qué métricas de rendimiento incluir en su informe personalizado.",
        "select_time_range_tooltip": "Establezca el período de tiempo histórico (en días) para el informe.",
        "select_visualization_type_tooltip": "Elija el tipo de gráfico para su informe personalizado.",
        "generate_report_button_tooltip": "Generar un informe personalizado basado en sus selecciones (conceptual).",
        "download_json_tooltip": "Descargar los datos JSON sin formato para el resultado de la simulación seleccionada.",
        "copy_json_tooltip": "Copiar los datos JSON sin formato al portapapeles.",
        "download_html_tooltip": "Generar y descargar un informe HTML básico.",
        "download_csv_tooltip": "Descargar un archivo CSV que contiene métricas clave para todas las ejecuciones en el resultado seleccionado.",
        "invalid_run_data_warning": "Los datos de ejecución faltan o están malformados, no se puede generar el gráfico.",
    }
)


# Entry point for the Streamlit app
if __name__ == "__main__":
    # Call the main dashboard function directly
    display_simulation_dashboard()
