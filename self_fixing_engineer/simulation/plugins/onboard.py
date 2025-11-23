import argparse
import ast
import asyncio
import contextlib
import functools
import getpass
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# Required library (fail fast)
try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError:
    sys.stderr.write(
        "Error: onboard.py requires 'pydantic'. Please install it: pip install pydantic\n"
    )
    sys.exit(97)

# Optional libraries
try:
    import colorama

    colorama.init()
    COLOR_OK = colorama.Fore.GREEN
    COLOR_WARN = colorama.Fore.YELLOW
    COLOR_ERR = colorama.Fore.RED
    COLOR_RESET = colorama.Style.RESET_ALL
except ImportError:
    COLOR_OK = COLOR_WARN = COLOR_ERR = COLOR_RESET = ""

try:
    from cryptography.fernet import Fernet

    crypto_available = True
except ImportError:
    crypto_available = False

try:
    import black  # only to detect availability; we will use CLI for stability

    black_available = True
except ImportError:
    black_available = False

# Tenacity is optional; provide no-op fallbacks if missing
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

    def retry(*args, **kwargs):
        def _wrap(fn):
            return fn

        return _wrap

    def stop_after_attempt(*_a, **_k):
        return None

    def wait_exponential(*_a, **_k):
        return None

    def retry_if_exception_type(*_a, **_k):
        return None


# HashiCorp Vault client (optional)
try:
    import hvac  # For HashiCorp Vault

    vault_available = True
except ImportError:
    vault_available = False

# Async file IO (optional)
try:
    import aiofiles

    aiofiles_available = True
except ImportError:
    aiofiles_available = False

# Prometheus client (optional)
try:
    from prometheus_client import Counter, Gauge, Histogram

    prometheus_available = True
except ImportError:
    prometheus_available = False

# Optional runtime backends (explicit imports so types are correct if available)
try:
    from mesh_adapter import MeshPubSub

    MESH_ADAPTER_AVAILABLE = True
except ImportError:
    MESH_ADAPTER_AVAILABLE = False

try:
    from checkpoint import CheckpointManager

    CHECKPOINT_AVAILABLE = True
except ImportError:
    CHECKPOINT_AVAILABLE = False

if sys.version_info < (3, 10):
    sys.stderr.write("Python 3.10+ required.\n")
    sys.exit(98)

# --- Dynamic Path Setup ---
script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
parent_dir = script_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))


# --- Configuration & Defaults ---
class OnboardConfig(BaseModel):
    project_type: str = Field(default="agentic_swarm", description="Type of project")
    plugins_dir: str = Field(default="./plugins", description="Directory for plugins")
    results_dir: str = Field(
        default="./simulation_results", description="Directory for results"
    )
    notification_backend: dict = Field(
        default_factory=dict, description="Notification backend config"
    )
    checkpoint_backend: dict = Field(
        default_factory=dict, description="Checkpoint backend config"
    )
    environment_variables: dict = Field(
        default_factory=dict, description="Environment variables to set"
    )
    generated_with: dict = Field(
        default_factory=dict, description="Generation metadata"
    )


# Load onboard_config from file with environment overrides
ONBOARD_CONFIG_FILE = script_dir / "configs" / "onboard_config.json"
try:
    if ONBOARD_CONFIG_FILE.exists():
        with open(ONBOARD_CONFIG_FILE, "r", encoding="utf-8") as f:
            file_config = json.load(f)
            # Override with env vars
            for key in ["plugins_dir", "results_dir"]:
                env_var = os.environ.get(f"OMNI_{key.upper()}")
                if env_var:
                    file_config[key] = env_var
            ONBOARD_DEFAULTS = OnboardConfig.parse_obj(file_config)
    else:
        ONBOARD_DEFAULTS = OnboardConfig()
except (FileNotFoundError, json.JSONDecodeError, ValidationError) as e:
    print(f"Warning: Failed to load/validate onboard_config.json: {e}. Using defaults.")
    ONBOARD_DEFAULTS = OnboardConfig()

# --- Logging Setup ---
LOG_FORMAT = "%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
logger = logging.getLogger("onboarding_wizard")
logger.setLevel(logging.INFO)


def setup_logging(verbose=False, quiet=False, json_format=False):
    level = logging.DEBUG if verbose else logging.ERROR if quiet else logging.INFO
    logger.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT)
    if json_format:
        formatter = logging.Formatter(
            '{"time": "%(asctime)s", "name": "%(name)s", "level": "%(levelname)s", "message": "%(message)s"}'
        )

    # Avoid duplicate handlers if rerun
    logger.handlers = []

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = RotatingFileHandler(
        script_dir / "onboarding_wizard.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # Always log debug to file
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


# --- Prometheus Metrics (with safe no-ops) ---
if prometheus_available:
    ONBOARDING_STEPS_TOTAL = Counter(
        "onboarding_steps_total", "Total onboarding steps completed", ["step"]
    )
    ONBOARDING_ERRORS_TOTAL = Counter(
        "onboarding_errors_total", "Total errors during onboarding", ["error_type"]
    )
    ONBOARDING_LATENCY_SECONDS = Histogram(
        "onboarding_latency_seconds", "Latency of onboarding operations", ["operation"]
    )
    ONBOARDING_HEALTH_CHECK_STATUS = Gauge(
        "onboarding_health_check_status", "Status of health checks", ["check_name"]
    )
else:

    class _NoopMetric:
        def labels(self, *a, **k):
            return self

        def time(self):
            return contextlib.nullcontext()

        def inc(self, *a, **k):
            pass

        def set(self, *a, **k):
            pass

    ONBOARDING_STEPS_TOTAL = ONBOARDING_ERRORS_TOTAL = ONBOARDING_LATENCY_SECONDS = (
        ONBOARDING_HEALTH_CHECK_STATUS
    ) = _NoopMetric()

# --- Configuration Paths ---
CONFIG_DIR = script_dir / "configs"
PLUGINS_DIR = script_dir / ONBOARD_DEFAULTS.plugins_dir
RESULTS_DIR = script_dir / ONBOARD_DEFAULTS.results_dir
CI_DIR = script_dir / ".github" / "workflows"
SECURE_CONFIG_PATH = CONFIG_DIR / "secure.json"
SECURE_KEY_PATH = CONFIG_DIR / "secure.key"  # separate key file

# Ensure directories exist
for d in (CONFIG_DIR, PLUGINS_DIR, RESULTS_DIR, CI_DIR):
    os.makedirs(d, exist_ok=True)

CORE_VERSION = "1.1.0"


def print_status(msg, level="info"):
    """Prints a formatted status message to the console."""
    prefix = "•"
    color = ""
    if level == "ok":
        prefix, color = "✔", COLOR_OK
    elif level == "warn":
        prefix, color = "!", COLOR_WARN
    elif level == "err":
        prefix, color = "✖", COLOR_ERR
    elif level == "info":
        prefix, color = "•", ""
    print(f"{color}{prefix} {msg}{COLOR_RESET}")

    if level == "err":
        ONBOARDING_ERRORS_TOTAL.labels(error_type="runtime_error").inc()
    else:
        ONBOARDING_STEPS_TOTAL.labels(step="print_status").inc()


def _non_interactive() -> bool:
    """Return True if running in CI or no TTY is attached."""
    return not sys.stdin.isatty() or _detect_ci()


def _get_user_input(
    prompt: str,
    options: Optional[List[str]] = None,
    default: Optional[str] = None,
    secret: bool = False,
) -> str:
    """Helper to get validated user input. secret=True uses getpass to avoid echo."""
    if _non_interactive():
        # In non-interactive mode, auto-return default if provided; otherwise fail fast.
        if default is not None:
            return default
        raise RuntimeError(
            f"Non-interactive mode: missing required input for prompt: {prompt}"
        )
    with ONBOARDING_LATENCY_SECONDS.labels(operation="get_user_input").time():
        while True:
            prompt_str = f"{prompt}"
            if options:
                prompt_str += f" ({'/'.join(options)})"
            if default is not None:
                prompt_str += f" [default: {default}]"
            prompt_str += ": "
            user_input = (
                getpass.getpass(prompt_str) if secret else input(prompt_str)
            ).strip()

            if not user_input and default is not None:
                return default
            if options:
                valid = [o.lower() for o in options]
                if user_input.lower() in valid:
                    return user_input.lower()
                print_status(f"Invalid input. Please choose from {options}.", "warn")
            elif user_input:
                return user_input
            else:
                print_status("Input cannot be empty.", "warn")


async def _generate_readme(target_dir: Path, title: str, description: str):
    """Generates a README.md file in the specified directory."""
    with ONBOARDING_LATENCY_SECONDS.labels(operation="generate_readme").time():
        readme_path = target_dir / "README.md"
        content = f"# {title}\n\n{description}\n\n_This file was auto-generated by the onboarding wizard._\n"
        try:
            if aiofiles_available:
                async with aiofiles.open(readme_path, "w", encoding="utf-8") as f:
                    await f.write(content)
            else:
                with open(readme_path, "w", encoding="utf-8") as f:
                    f.write(content)
            logger.info(f"Generated README: {readme_path}")
        except (PermissionError, OSError) as e:
            logger.exception(f"Failed to write README: {e}")
            print_status(
                f"Failed to write README: {readme_path}. Check permissions.", "err"
            )


async def _generate_config(config_data: Dict[str, Any], filename: str = "config.json"):
    """Writes the generated configuration to a JSON file."""
    with ONBOARDING_LATENCY_SECONDS.labels(operation="generate_config").time():
        config_path = CONFIG_DIR / filename

        # Pydantic validation (best effort)
        try:
            OnboardConfig.parse_obj(
                {
                    "project_type": config_data.get("project_type", ""),
                    "plugins_dir": config_data.get("plugins_dir", ""),
                    "results_dir": config_data.get("results_dir", ""),
                    "notification_backend": config_data.get("notification_backend", {}),
                    "checkpoint_backend": config_data.get("checkpoint_backend", {}),
                    "environment_variables": config_data.get(
                        "environment_variables", {}
                    ),
                    "generated_with": config_data.get("generated_with", {}),
                }
            )
        except ValidationError as e:
            print_status(f"Generated config failed validation: {e}", "warn")
            logger.warning(f"Config validation warning: {e}")

        try:
            payload = json.dumps(config_data, indent=2, sort_keys=True)
            if aiofiles_available:
                async with aiofiles.open(config_path, "w", encoding="utf-8") as f:
                    await f.write(payload)
            else:
                with open(config_path, "w", encoding="utf-8") as f:
                    f.write(payload)
            print_status(f"Generated configuration file: {config_path}", "ok")
            logger.info(f"Generated configuration file: {config_path}")
        except (PermissionError, OSError) as e:
            print_status(
                f"Failed to write config file {config_path}: {e}. Check permissions or disk space.",
                "err",
            )
            logger.exception(f"Failed to write config file {config_path}: {e}")
            raise


def _read_or_create_key() -> Optional[bytes]:
    """
    Load Fernet key from OMNI_FERNET_KEY or secure.key. If not present, generate a new one and persist to secure.key with 0o600 perms.
    Returns bytes key or None if crypto unavailable or invalid.
    """
    if not crypto_available:
        return None

    env_key = os.environ.get("OMNI_FERNET_KEY")
    if env_key:
        try:
            # Validate the supplied key immediately
            Fernet(env_key.encode("utf-8"))
            return env_key.encode("utf-8")
        except Exception:
            logger.error(
                "Invalid OMNI_FERNET_KEY: must be a valid Fernet (base64 urlsafe) key."
            )
            return None

    try:
        if SECURE_KEY_PATH.exists():
            with open(SECURE_KEY_PATH, "rb") as kf:
                key = kf.read().strip()
            # Validate loaded key
            try:
                Fernet(key)
            except Exception:
                logger.error("Invalid key found in secure.key; regenerate it.")
                return None
            return key
        # Generate and persist
        key = Fernet.generate_key()
        with open(SECURE_KEY_PATH, "wb") as kf:
            kf.write(key)
        os.chmod(SECURE_KEY_PATH, 0o600)
        return key
    except Exception as e:
        logger.error(f"Failed to access/generate Fernet key: {e}", exc_info=True)
        return None


def _generate_secure_config(secrets: Dict[str, str], secrets_manager: str = "local"):
    """Stores secrets using selected manager."""
    with ONBOARDING_LATENCY_SECONDS.labels(operation="generate_secure_config").time():
        if secrets_manager == "vault" and vault_available:
            vault_url = os.environ.get("VAULT_URL", "http://localhost:8200")
            vault_token = os.environ.get("VAULT_TOKEN") or _get_user_input(
                "Enter Vault Token", secret=True
            )
            try:
                client = hvac.Client(url=vault_url, token=vault_token)
                # Store secrets directly under kv v2 path
                client.secrets.kv.v2.create_or_update_secret(
                    path="onboard-secrets", secret=secrets
                )
                print_status("Secrets stored in HashiCorp Vault.", "ok")
            except Exception as e:
                print_status(f"Failed to store secrets in Vault: {e}", "err")
                logger.exception("Failed to store secrets in Vault.")
                raise
            return

        if secrets_manager in {"aws", "azure"}:
            # Do not silently fall back to local when a cloud manager was selected
            msg = f"{secrets_manager.upper()} Secrets Manager integration not implemented yet. Aborting secret storage."
            print_status(msg, "err")
            raise RuntimeError(msg)

        if not crypto_available:
            print_status(
                "cryptography library not available. Refusing to store secrets insecurely. Please install 'cryptography' or use a secret manager.",
                "err",
            )
            raise RuntimeError(
                "Cannot securely store secrets without 'cryptography' or a secrets manager."
            )

        key = _read_or_create_key()
        if not key:
            print_status(
                "Failed to provision encryption key. Secrets will not be stored.", "err"
            )
            raise RuntimeError("Encryption key unavailable.")

        f = Fernet(key)
        encrypted_secrets = {
            k: f.encrypt(v.encode()).decode() for k, v in secrets.items()
        }
        data = {"secrets": encrypted_secrets}

        with open(SECURE_CONFIG_PATH, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        os.chmod(SECURE_CONFIG_PATH, 0o600)
        print_status(f"Encrypted secrets stored in {SECURE_CONFIG_PATH}", "ok")


def _load_secure_config() -> Dict[str, str]:
    """Loads and decrypts secrets from secure.json using Fernet key from env or secure.key."""
    if not SECURE_CONFIG_PATH.exists():
        return {}

    with open(SECURE_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not crypto_available:
        print_status(
            "cryptography not available. Encrypted secrets cannot be decrypted.", "err"
        )
        return {}

    key = _read_or_create_key()
    if not key:
        print_status("Missing encryption key. Cannot decrypt secrets.", "err")
        return {}

    f = Fernet(key)
    try:
        return {
            k: f.decrypt(v.encode("utf-8")).decode("utf-8")
            for k, v in data.get("secrets", {}).items()
        }
    except Exception as e:
        logger.error(f"Failed to decrypt secrets: {e}", exc_info=True)
        print_status("Failed to decrypt secrets. Check your key.", "err")
        return {}


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(requests.RequestException),
)
def _send_telemetry(data: Dict[str, Any]):
    """Sends anonymized telemetry data (opt-in)."""
    requests.post("https://telemetry.omnisapient.ai/collect", json=data, timeout=5)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=10),
    retry=retry_if_exception_type(Exception),
)
def _load_secrets_from_vault(vault_url: str, vault_token: str) -> Dict[str, str]:
    """Loads secrets from a HashiCorp Vault KV v2 path."""
    if not vault_available:
        raise ImportError("hvac library not available")

    @functools.lru_cache(maxsize=8)
    def get_vault_client(url: str, token: str):
        return hvac.Client(url=url, token=token)

    client = get_vault_client(vault_url, vault_token)
    record = client.secrets.kv.v2.read_secret_version(path="onboard-secrets")
    # kv v2: response['data']['data'] contains the secret dict
    return record.get("data", {}).get("data", {}) or {}


def _validate_plugin_syntax(plugin_file_path: Path):
    """Validates Python plugin syntax using ast.parse."""
    try:
        with open(plugin_file_path, "r", encoding="utf-8") as f:
            ast.parse(f.read())
        print_status(f"Plugin syntax validation passed for {plugin_file_path}", "ok")
    except SyntaxError as e:
        print_status(
            f"Plugin syntax validation failed for {plugin_file_path}: {e}. Check syntax errors in the file.",
            "err",
        )
        raise


def _auto_format_plugin(plugin_file_path: Path):
    if black_available:
        try:
            # Use CLI for stability across versions
            subprocess.run(
                [sys.executable, "-m", "black", str(plugin_file_path), "--quiet"],
                check=False,
            )
            print_status(f"Auto-formatted plugin with black: {plugin_file_path}", "ok")
        except Exception as e:
            print_status(f"Failed to auto-format {plugin_file_path}: {e}", "warn")
    else:
        print_status("black not available, skipping auto-formatting.", "warn")


async def _generate_plugin_manifest(
    plugin_type: str, plugin_name: str, plugins_dir: Path
):
    """Generates a basic plugin manifest and a dummy plugin file for demonstration."""
    with ONBOARDING_LATENCY_SECONDS.labels(operation="generate_plugin_manifest").time():
        manifest = {
            "name": plugin_name,
            "version": "0.0.1",
            "description": f"A demo {plugin_type} plugin generated by the onboarding wizard.",
            "entrypoint": f"{plugin_name}.py" if plugin_type == "python" else "main",
            "type": plugin_type,
            "author": "Omnisapient Wizard",
            "capabilities": ["demo_capability"],
            "permissions": ["none"],
            "dependencies": [],
            "min_core_version": "1.1.0",
            "max_core_version": "2.0.0",
            "health_check": "plugin_health",
            "api_version": "v1",
            "license": "MIT",
            "homepage": "",
            "tags": ["demo", "onboarding"],
            "generated_with": {
                "wizard_version": "1.0.0",
                "python_version": platform.python_version(),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
        if (
            manifest["min_core_version"] > CORE_VERSION
            or manifest["max_core_version"] < CORE_VERSION
        ):
            print_status(
                f"Generated plugin may not be compatible with core version {CORE_VERSION}",
                "warn",
            )

        if plugin_type == "python":
            plugin_file_content = f"""# --- Demo Python Plugin Generated by Onboarding Wizard ---
PLUGIN_MANIFEST = {json.dumps(manifest, indent=4)}

def plugin_health():
    # Simple health check for demo purposes
    return {{"status": "ok", "message": "Demo Python plugin is healthy!"}}

class PLUGIN_API:
    def hello(self):
        return "Hello from the onboard demo Python plugin!"

if __name__ == "__main__":
    import json as _json
    print(_json.dumps(PLUGIN_MANIFEST, indent=4))
    print(plugin_health())
    print(PLUGIN_API().hello())
"""
            plugin_filepath = plugins_dir / f"{plugin_name}.py"
            try:
                if aiofiles_available:
                    async with aiofiles.open(
                        plugin_filepath, "w", encoding="utf-8"
                    ) as f:
                        await f.write(plugin_file_content)
                else:
                    with open(plugin_filepath, "w", encoding="utf-8") as f:
                        f.write(plugin_file_content)

                _validate_plugin_syntax(plugin_filepath)
                _auto_format_plugin(plugin_filepath)
                print_status(
                    f"Generated demo Python plugin file: {plugin_filepath}", "ok"
                )
                logger.info(f"Generated demo Python plugin file: {plugin_filepath}")
                await _generate_readme(
                    plugins_dir,
                    "Plugins Directory",
                    "Drop Python plugins or plugin folders here. Each plugin should have a manifest or be a single .py file.",
                )
            except (PermissionError, OSError) as e:
                print_status(
                    f"Failed to write demo plugin file {plugin_filepath}: {e}. Check permissions or syntax in template.",
                    "err",
                )
                logger.exception(
                    f"Failed to write demo plugin file {plugin_filepath}: {e}"
                )
                raise
        else:
            plugin_dir = plugins_dir / plugin_name
            os.makedirs(plugin_dir, exist_ok=True)
            manifest_path = plugin_dir / "manifest.json"
            try:
                payload = json.dumps(manifest, indent=2)
                if aiofiles_available:
                    async with aiofiles.open(manifest_path, "w", encoding="utf-8") as f:
                        await f.write(payload)
                else:
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        f.write(payload)

                print_status(
                    f"Generated demo {plugin_type} plugin manifest: {manifest_path}",
                    "ok",
                )
                logger.info(
                    f"Generated demo {plugin_type} plugin manifest: {manifest_path}"
                )

                if plugin_type == "wasm":
                    wasm_filepath = plugin_dir / f"{plugin_name}.wasm"
                    with open(wasm_filepath, "wb") as f:
                        f.write(b"\x00\x61\x73\x6d\x01\x00\x00\x00")
                    print_status(
                        f"Created placeholder WASM file: {wasm_filepath}. Replace with your compiled .wasm binary.",
                        "info",
                    )

                await _generate_readme(
                    plugin_dir,
                    f"{plugin_type.capitalize()} Plugin Directory",
                    f"This folder contains a demo manifest for a {plugin_type} plugin. Add your plugin implementation here.",
                )
            except (PermissionError, OSError) as e:
                print_status(
                    f"Failed to write demo plugin manifest {manifest_path}: {e}. Check permissions.",
                    "err",
                )
                logger.exception(
                    f"Failed to write demo plugin manifest {manifest_path}: {e}"
                )
                raise


async def _run_health_checks(config: Dict[str, Any]):
    """Runs health checks for configured backends and installed plugins."""
    print_status("\n--- Running Health Checks ---", "ok")

    # Pub/Sub Health Check
    if MESH_ADAPTER_AVAILABLE:
        pubsub_backend_url = config.get("notification_backend", {}).get("url")
        if pubsub_backend_url:
            print_status(f"Checking Pub/Sub backend: {pubsub_backend_url}")
            try:
                mesh_kwargs = {}
                if pubsub_backend_url.startswith("gcs://"):
                    mesh_kwargs["gcs_bucket_name"] = config["notification_backend"].get(
                        "gcs_bucket_name"
                    )
                elif pubsub_backend_url.startswith("azure://"):
                    mesh_kwargs["azure_connection_string"] = config[
                        "notification_backend"
                    ].get("azure_connection_string")
                    mesh_kwargs["azure_container_name"] = config[
                        "notification_backend"
                    ].get("azure_container_name")
                elif pubsub_backend_url.startswith("etcd://"):
                    mesh_kwargs["etcd_host"] = config["notification_backend"].get(
                        "etcd_host"
                    )
                    mesh_kwargs["etcd_port"] = int(
                        config["notification_backend"].get("etcd_port", 2379)
                    )

                mesh = MeshPubSub(backend_url=pubsub_backend_url, **mesh_kwargs)
                await mesh.connect()
                health = await mesh.healthcheck()
                ok = health.get("status") == "ok"
                print_status(
                    f"Pub/Sub Health: {health.get('status','unknown').upper()} - {health.get('message', '')}",
                    "ok" if ok else "err",
                )
                ONBOARDING_HEALTH_CHECK_STATUS.labels(check_name="pubsub").set(
                    1 if ok else 0
                )
                await mesh.close()
            except Exception as e:
                print_status(f"Pub/Sub Health Check FAILED: {e}", "err")
                logger.error(f"Pub/Sub Health Check FAILED: {e}")
                ONBOARDING_HEALTH_CHECK_STATUS.labels(check_name="pubsub").set(0)
        else:
            print_status("No Pub/Sub backend configured for health check.")
    else:
        print_status("MeshPubSub not available, skipping Pub/Sub health check.", "warn")

    # Checkpoint Health Check
    if CHECKPOINT_AVAILABLE:
        checkpoint_backend_type = config.get("checkpoint_backend", {}).get("type")
        if checkpoint_backend_type:
            print_status(f"Checking Checkpoint backend: {checkpoint_backend_type}")
            try:
                chk_manager_kwargs = {"backend": checkpoint_backend_type}
                backend_config_key = f"{checkpoint_backend_type}_config"
                if backend_config_key in config.get("checkpoint_backend", {}):
                    chk_manager_kwargs[backend_config_key] = config[
                        "checkpoint_backend"
                    ][backend_config_key]

                if checkpoint_backend_type == "fs":
                    fs_dir = config["checkpoint_backend"].get("dir", "./checkpoints")
                    os.makedirs(fs_dir, exist_ok=True)
                    chk_manager_kwargs["dir"] = fs_dir

                chk = CheckpointManager(**chk_manager_kwargs)

                test_data = {"status": "healthy", "timestamp": time.time()}
                test_name = "onboarding_health_test"
                try:
                    await chk.delete(test_name)
                except Exception:
                    pass

                await chk.save(test_name, test_data)
                loaded_data = await chk.load(test_name)
                await chk.delete(test_name)

                if loaded_data and loaded_data.get("status") == "healthy":
                    print_status(
                        f"Checkpoint Health: OK (saved and loaded test data successfully for {checkpoint_backend_type}).",
                        "ok",
                    )
                    ONBOARDING_HEALTH_CHECK_STATUS.labels(check_name="checkpoint").set(
                        1
                    )
                else:
                    print_status(
                        f"Checkpoint Health FAILED: Data mismatch for {checkpoint_backend_type}.",
                        "err",
                    )
                    ONBOARDING_HEALTH_CHECK_STATUS.labels(check_name="checkpoint").set(
                        0
                    )
            except Exception as e:
                print_status(
                    f"Checkpoint Health Check FAILED for {checkpoint_backend_type}: {e}",
                    "err",
                )
                logger.error(
                    f"Checkpoint Health Check FAILED for {checkpoint_backend_type}: {e}"
                )
                ONBOARDING_HEALTH_CHECK_STATUS.labels(check_name="checkpoint").set(0)
        else:
            print_status("No Checkpoint backend configured for health check.")
    else:
        print_status(
            "CheckpointManager not available, skipping Checkpoint health check.", "warn"
        )

    print_status(
        "\nChecking installed demo plugins (requires PluginManager to be run separately for full check):",
        "info",
    )
    for plugin_type in ["python", "wasm", "grpc"]:
        plugin_name = f"demo_{plugin_type}_plugin"
        plugin_manifest_path = PLUGINS_DIR / plugin_name / "manifest.json"
        plugin_file_path = PLUGINS_DIR / f"{plugin_name}.py"

        if plugin_file_path.exists() or plugin_manifest_path.exists():
            print_status(
                f"  - Demo {plugin_type} plugin detected. (Run your plugin manager for full health)",
                "info",
            )
        else:
            print_status(f"  - No demo {plugin_type} plugin installed.", "info")

    print_status("--- Health Checks Complete ---", "ok")


async def _safe_mode_profile():
    """Generate and write Safe Mode/Starter Profile for quick demo/local run."""
    print_status(
        "*** SAFE MODE / STARTER PROFILE: Local-only demo. No cloud/network required. ***",
        "ok",
    )

    core_config = {
        "project_type": "demo_safe_mode",
        "plugins_dir": str(PLUGINS_DIR),
        "results_dir": str(RESULTS_DIR),
        "notification_backend": {"type": "local", "url": "local://"},
        "checkpoint_backend": {"type": "fs", "dir": "./checkpoints"},
        "environment_variables": {
            "MESH_BACKEND_URL": "local://",
            "CHECKPOINT_BACKEND_TYPE": "fs",
            "CHECKPOINT_FS_DIR": "./checkpoints",
        },
        "generated_with": {
            "wizard_version": "1.0.0",
            "python_version": platform.python_version(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    }

    await _generate_config(core_config)
    await _generate_readme(
        CONFIG_DIR,
        "Configs Directory",
        "Contains configuration files for your project (SAFE MODE).",
    )

    plugin_name = "demo_python_plugin"
    await _generate_plugin_manifest("python", plugin_name, PLUGINS_DIR)

    await _generate_readme(
        PLUGINS_DIR,
        "Plugins Directory",
        "Drop Python plugins or plugin folders here. Each plugin should have a manifest or be a single .py file. (SAFE MODE)",
    )
    await _generate_readme(
        RESULTS_DIR,
        "Simulation Results",
        "Simulation outputs and logs will be stored here. (SAFE MODE)",
    )

    print_status(
        "Safe Mode profile created. You can run a local-only demo without any cloud/network setup!",
        "ok",
    )
    print_status("To reset at any time, run: python onboard.py --reset", "info")
    print_status(
        "To run troubleshooting/diagnostics, run: python onboard.py --troubleshoot",
        "info",
    )


async def _reset_to_safe_mode():
    """Reset configs/plugins to safe mode profile."""
    print_status("*** RESETTING TO SAFE MODE / STARTER PROFILE ***", "warn")
    # Extra confirmation for interactive mode
    if not _non_interactive():
        confirm = _get_user_input(
            "This will delete configs/plugins/results/CI files in this repo. Type YES to confirm",
            default="no",
        )
    else:
        confirm = "YES"  # non-interactive: assume explicit --reset implies confirmation
    if confirm != "YES":
        print_status("Reset aborted.", "warn")
        return

    for target_dir in [CONFIG_DIR, PLUGINS_DIR, RESULTS_DIR, CI_DIR]:
        if target_dir.exists():
            for fpath in target_dir.iterdir():
                try:
                    if fpath.is_dir():
                        shutil.rmtree(fpath, ignore_errors=True)
                    elif fpath.name not in (
                        ".gitkeep",
                        ".gitignore",
                    ) and not fpath.name.endswith(".md"):
                        fpath.unlink()
                except (PermissionError, OSError):
                    logger.warning(f"Failed to clean up {fpath}. Skipping.")

    await _safe_mode_profile()
    print_status("Platform reset to Safe Mode/Starter Profile.", "ok")


def _run_basic_onboarding_tests():
    """Runs basic tests to verify core setup after onboarding."""
    print_status("\n--- Running Basic Onboarding Tests ---", "ok")
    test_config_path = CONFIG_DIR / "config.json"

    if test_config_path.exists():
        print_status(f"Test 1 (Config File): {test_config_path} exists.", "ok")
        try:
            with open(test_config_path, "r", encoding="utf-8") as f:
                test_config = json.load(f)
            assert "project_type" in test_config
            assert "notification_backend" in test_config
            assert "checkpoint_backend" in test_config
            print_status("Test 1 (Config Content): Basic fields present. PASSED.", "ok")
        except (json.JSONDecodeError, AssertionError, Exception) as e:
            print_status(f"Test 1 (Config Content): FAILED - {e}", "err")
            logger.error(f"Test 1 (Config Content): FAILED - {e}")
    else:
        print_status("Test 1 (Config File): FAILED - config.json not found.", "err")
        logger.error("Test 1 (Config File): FAILED - config.json not found.")

    test_plugin_name = "demo_python_plugin"
    test_plugin_manifest_path = PLUGINS_DIR / test_plugin_name / "manifest.json"
    test_plugin_file_path = PLUGINS_DIR / f"{test_plugin_name}.py"

    if test_plugin_file_path.exists():
        print_status(f"Test 2 (Python Plugin): {test_plugin_file_path} exists.", "ok")
        try:
            with open(test_plugin_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "PLUGIN_MANIFEST" in content
            assert "plugin_health" in content
            print_status(
                "Test 2 (Python Plugin Content): Basic structure present. PASSED.", "ok"
            )
        except (AssertionError, Exception) as e:
            print_status(f"Test 2 (Python Plugin Content): FAILED - {e}", "err")
            logger.error(f"Test 2 (Python Plugin Content): FAILED - {e}")
    elif test_plugin_manifest_path.exists():
        print_status(
            f"Test 2 (Non-Python Plugin): {test_plugin_manifest_path} exists.", "ok"
        )
        try:
            with open(test_plugin_manifest_path, "r", encoding="utf-8") as f:
                manifest_content = json.load(f)
            assert manifest_content.get("name") == test_plugin_name
            print_status(
                "Test 2 (Non-Python Plugin Content): Manifest present. PASSED.", "ok"
            )
        except (json.JSONDecodeError, AssertionError, Exception) as e:
            print_status(f"Test 2 (Non-Python Plugin Content): FAILED - {e}", "err")
            logger.error(f"Test 2 (Non-Python Plugin Content): FAILED - {e}")
    else:
        print_status(
            "Test 2 (Demo Plugin): Skipped, no demo plugin file/manifest found (expected if no plugin types were selected).",
            "warn",
        )

    print_status("--- Basic Onboarding Tests Complete ---", "ok")


def _generate_ci_yaml(ci_env=False):
    """Generates a basic GitHub Actions CI YAML file."""
    ci_path = CI_DIR / "ci.yaml"
    ci_content = """
name: Python CI

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    - name: Set up Python 3.10
      uses: actions/setup-python@v5
      with:
        python-version: '3.10'
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install flake8 pytest
        if [ -f requirements.txt ]; then pip install -r requirements.txt; fi
    - name: Lint with flake8
      run: |
        flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
        flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics
    - name: Test with pytest
      run: |
        pytest
"""
    try:
        with open(ci_path, "w", encoding="utf-8") as f:
            f.write(ci_content)
        print_status(f"Generated CI YAML: {ci_path}", "ok")
        if ci_env:
            print_status(
                "Detected CI environment. Use the generated ci.yaml in your workflow.",
                "info",
            )
    except (PermissionError, OSError) as e:
        print_status(f"Failed to generate CI YAML: {e}", "err")


def _print_help():
    """Prints the help message for the onboarding wizard."""
    print(
        """
Self-Fixing Engineer Platform Onboarding Wizard

Usage: python onboard.py [OPTIONS]

Options:
  --safe                     Run onboarding in SAFE MODE / Starter Profile (local demo only)
  --reset                    Reset configs/plugins to safe mode / starter profile
  --troubleshoot             Run health checks and onboarding diagnostics
  --show-examples            Show example configs, plugins, jobs after onboarding
  --verbose                  Show detailed output
  --quiet                    Suppress non-essential output
  --json-log                 Enable structured JSON logging
  --project-type TYPE        Set project type (no prompt)
  --plugin-types TYPES       Set plugin types (comma-separated, no prompt)
  --pubsub-backend BACKEND   Set pubsub backend (no prompt)
  --checkpoint-backend BACKEND Set checkpoint backend (no prompt)
  -h, --help                 Show this help message
"""
    )


def _check_existing_configs():
    """Check if configs/plugins exist and warn."""
    existing = any(d.exists() and any(d.iterdir()) for d in [CONFIG_DIR, PLUGINS_DIR])
    if existing:
        try:
            overwrite = _get_user_input(
                "Existing configs/plugins detected. Continue and overwrite?",
                options=["yes", "no"],
                default="no",
            )
        except RuntimeError:
            # Non-interactive: do not overwrite by default
            print_status(
                "Existing configs/plugins detected. Use --reset or remove the directories to continue in non-interactive mode.",
                "err",
            )
            sys.exit(2)
        if overwrite == "no":
            sys.exit(0)


def _detect_venv():
    if sys.prefix == sys.base_prefix:
        print_status(
            "WARNING: Not running in a Python virtual environment. Consider using venv for isolation.",
            "warn",
        )


def _detect_ci():
    return bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))


def _print_security_checklist(config: Dict[str, Any]):
    print_status("\n--- Security Checklist ---", "ok")
    if SECURE_CONFIG_PATH.exists():
        print_status("Secrets encrypted at rest (secure.json present).", "ok")
    else:
        print_status(
            "No encrypted secrets found - configure encryption or secrets manager.",
            "warn",
        )
    print_status(
        (
            "Default config disables external network access in Safe Mode."
            if config.get("project_type") == "demo_safe_mode"
            else "External access enabled - review permissions."
        ),
        "ok" if config.get("project_type") == "demo_safe_mode" else "warn",
    )
    print_status("Health checks completed (check logs for details).", "ok")
    print_status("--- End Security Checklist ---", "ok")


def _show_examples():
    print_status("\n--- Example Config Snippet ---", "info")
    print(json.dumps({"project_type": "example", "plugins_dir": "./plugins"}, indent=2))
    print_status("\n--- Example Plugin Manifest ---", "info")
    print(json.dumps({"name": "example_plugin", "version": "1.0"}, indent=2))
    print_status("\n--- Example Job JSON ---", "info")
    print(json.dumps({"job_id": "123", "status": "pending"}, indent=2))


def _auto_open_docs():
    readme_path = script_dir / "README.md"
    if readme_path.exists() and not _non_interactive():
        open_docs = _get_user_input(
            "Open README in default browser?", options=["yes", "no"], default="yes"
        )
        if open_docs == "yes":
            webbrowser.open(f"file://{os.path.abspath(readme_path)}")


def _print_support_links():
    print_status("\n--- Support & Docs ---", "info")
    print_status("Docs: https://docs.omnisapient.ai")
    print_status("Discord: https://discord.gg/omnisapient")
    print_status("Support: support@omnisapient.ai")


def _cleanup_partial_onboard():
    """Cleans up partially generated files on failure."""
    for target_dir in [CONFIG_DIR, PLUGINS_DIR, RESULTS_DIR, CI_DIR]:
        if target_dir.exists():
            for fpath in target_dir.iterdir():
                try:
                    if fpath.is_dir():
                        shutil.rmtree(fpath, ignore_errors=True)
                    else:
                        fpath.unlink()
                except (PermissionError, OSError):
                    logger.warning(f"Failed to clean up {fpath}. Skipping.")


async def _test_connection(backend: str, config: Dict[str, Any]):
    """Live test connection for cloud backends (best effort)."""
    if backend == "aws":
        try:
            import boto3

            session = boto3.Session(
                aws_access_key_id=config["secrets"].get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=config["secrets"].get("AWS_SECRET_ACCESS_KEY"),
                region_name=config["secrets"].get("AWS_REGION_NAME"),
            )
            s3 = session.client("s3")
            s3.list_buckets()
            print_status("AWS connection test passed.", "ok")
        except ImportError:
            print_status("Boto3 not installed. Skipping AWS test.", "warn")
        except Exception as e:
            print_status(f"AWS connection test failed: {e}", "err")
            ONBOARDING_HEALTH_CHECK_STATUS.labels(check_name="aws_connection").set(0)
    elif backend == "gcs":
        try:
            from google.cloud import storage

            client = storage.Client()
            list(client.list_buckets())
            print_status("GCS connection test passed.", "ok")
        except ImportError:
            print_status(
                "Google Cloud Storage library not installed. Skipping GCS test.", "warn"
            )
        except Exception as e:
            print_status(f"GCS connection test failed: {e}", "err")
            ONBOARDING_HEALTH_CHECK_STATUS.labels(check_name="gcs_connection").set(0)


async def onboard(args):
    """
    CLI Quickstart Wizard for setting up the Omnisapient project.
    Walks the user through configuring project type, plugin types, notification
    and checkpoint backends, and generates starter files.
    """
    try:
        _check_existing_configs()
        _detect_venv()
        ci_env = _detect_ci()
        non_interactive = _non_interactive()

        ONBOARDING_STEPS_TOTAL.labels(step="start").inc()

        print_status(
            "--- Welcome to the Omnisapient AI Project Onboarding Wizard! ---", "ok"
        )
        print_status(
            "This wizard will help you set up your project configuration and a demo plugin.",
            "info",
        )

        # --- Project type ---
        if args.project_type:
            project_type = args.project_type
        else:
            project_type = (
                "agentic_swarm"
                if non_interactive
                else _get_user_input(
                    "What type of project are you building?",
                    options=["agentic_swarm", "simulation", "rl_environment", "other"],
                    default="agentic_swarm",
                )
            )
        print_status(f"Project type selected: {project_type}")
        logger.info(f"User selected project type: {project_type}")

        # --- Plugin Setup ---
        print_status("\n--- Plugin Configuration ---", "ok")
        plugin_types_options = ["python", "wasm", "grpc"]
        if args.plugin_types:
            selected_plugin_types_input = args.plugin_types
        else:
            selected_plugin_types_input = (
                "python"
                if non_interactive
                else _get_user_input(
                    f"Which plugin types do you plan to use? (comma-separated, e.g., {','.join(plugin_types_options)})",
                    default="python",
                )
            )
        selected_plugin_types = [
            p.strip()
            for p in selected_plugin_types_input.split(",")
            if p.strip() in plugin_types_options
        ]
        if not selected_plugin_types:
            print_status(
                "No valid plugin types selected. Skipping demo plugin generation.",
                "warn",
            )
            logger.warning("No valid plugin types selected by user.")
        else:
            print_status(f"Selected plugin types: {', '.join(selected_plugin_types)}")
            logger.info(f"User selected plugin types: {selected_plugin_types}")

        # --- Notification Backend Setup (MeshPubSub) ---
        print_status(
            "\n--- Notification Backend Setup (for real-time events) ---", "ok"
        )
        pubsub_backend_options = ["local"]
        if MESH_ADAPTER_AVAILABLE:
            try:
                supported = getattr(
                    MeshPubSub,
                    "supported_backends",
                    lambda: [
                        "redis",
                        "nats",
                        "kafka",
                        "rabbitmq",
                        "aws",
                        "gcs",
                        "azure",
                        "etcd",
                    ],
                )()
                pubsub_backend_options = sorted(
                    list(
                        set(
                            pubsub_backend_options
                            + [b for b in supported if b != "local"]
                        )
                    )
                )
            except Exception as e:
                logger.warning(
                    f"Could not get supported backends from MeshPubSub: {e}. Using default set."
                )
                pubsub_backend_options = sorted(
                    list(
                        set(
                            pubsub_backend_options
                            + [
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
                    )
                )

        if args.pubsub_backend:
            pubsub_backend = args.pubsub_backend
        else:
            pubsub_backend = (
                "local"
                if non_interactive
                else _get_user_input(
                    f"Choose your preferred notification backend: {pubsub_backend_options}",
                    options=pubsub_backend_options,
                    default=(
                        "redis"
                        if "redis" in pubsub_backend_options
                        else pubsub_backend_options[0]
                    ),
                )
            )

        pubsub_config = {"type": pubsub_backend}
        secrets = {}
        secrets_manager = "local"
        if pubsub_backend in ["aws", "gcs", "azure"]:
            if not non_interactive:
                use_secrets_manager = _get_user_input(
                    "Store secrets with advanced manager (vault/aws/azure)?",
                    options=["yes", "no"],
                    default="no",
                )
                if use_secrets_manager == "yes":
                    secrets_manager = _get_user_input(
                        "Choose manager",
                        options=["vault", "aws", "azure"],
                        default="vault",
                    )
            else:
                # non-interactive: expect env vars or skip secret storage
                secrets_manager = os.environ.get("OMNI_SECRETS_MANAGER", "local")

        if pubsub_backend == "redis":
            pubsub_config["url"] = (
                "redis://localhost:6379/0"
                if non_interactive
                else _get_user_input(
                    "Enter Redis URL", default="redis://localhost:6379/0"
                )
            )
        elif pubsub_backend == "nats":
            pubsub_config["url"] = (
                "nats://localhost:4222"
                if non_interactive
                else _get_user_input("Enter NATS URL", default="nats://localhost:4222")
            )
        elif pubsub_backend == "kafka":
            pubsub_config["url"] = (
                "localhost:9092"
                if non_interactive
                else _get_user_input(
                    "Enter Kafka Bootstrap Servers (e.g., localhost:9092)",
                    default="localhost:9092",
                )
            )
        elif pubsub_backend == "rabbitmq":
            pubsub_config["url"] = (
                "amqp://guest:guest@localhost:5672/"
                if non_interactive
                else _get_user_input(
                    "Enter RabbitMQ AMQP URL",
                    default="amqp://guest:guest@localhost:5672/",
                )
            )
        elif pubsub_backend == "aws":
            pubsub_config["url"] = "aws://"
            secrets["AWS_ACCESS_KEY_ID"] = os.environ.get("AWS_ACCESS_KEY_ID") or (
                None if non_interactive else _get_user_input("Enter AWS Access Key ID")
            )
            secrets["AWS_SECRET_ACCESS_KEY"] = os.environ.get(
                "AWS_SECRET_ACCESS_KEY"
            ) or (
                None
                if non_interactive
                else _get_user_input("Enter AWS Secret Access Key", secret=True)
            )
            secrets["AWS_REGION_NAME"] = os.environ.get("AWS_REGION_NAME") or (
                "us-east-1"
                if non_interactive
                else _get_user_input("Enter AWS Region", default="us-east-1")
            )
            print_status(
                "For AWS backend, secrets will be stored when provided.", "info"
            )
            if not non_interactive:
                test_conn = _get_user_input(
                    "Test AWS connection now?", options=["yes", "no"], default="yes"
                )
                if test_conn == "yes":
                    await _test_connection("aws", {"secrets": secrets})
        elif pubsub_backend == "gcs":
            pubsub_config["url"] = "gcs://"
            pubsub_config["gcs_bucket_name"] = (
                "your-gcs-event-bucket"
                if non_interactive
                else _get_user_input(
                    "Enter GCS Bucket Name for events", default="your-gcs-event-bucket"
                )
            )
            secrets["GOOGLE_APPLICATION_CREDENTIALS"] = os.environ.get(
                "GOOGLE_APPLICATION_CREDENTIALS"
            ) or (
                None
                if non_interactive
                else _get_user_input("Enter path to Google Service Account Key")
            )
            if not non_interactive:
                test_conn = _get_user_input(
                    "Test GCS connection now?", options=["yes", "no"], default="yes"
                )
                if test_conn == "yes":
                    await _test_connection("gcs", {"secrets": secrets})
        elif pubsub_backend == "azure":
            pubsub_config["url"] = "azure://"
            pubsub_config["azure_connection_string"] = (
                "DefaultEndpointsProtocol=..."
                if non_interactive
                else _get_user_input(
                    "Enter Azure Storage Connection String",
                    default="DefaultEndpointsProtocol=...",
                )
            )
            pubsub_config["azure_container_name"] = (
                "mesh-events"
                if non_interactive
                else _get_user_input(
                    "Enter Azure Blob Container Name for events", default="mesh-events"
                )
            )
        elif pubsub_backend == "etcd":
            pubsub_config["url"] = "etcd://"
            pubsub_config["etcd_host"] = (
                "localhost"
                if non_interactive
                else _get_user_input("Enter etcd Host", default="localhost")
            )
            pubsub_config["etcd_port"] = (
                "2379"
                if non_interactive
                else _get_user_input("Enter etcd Port", default="2379")
            )
        elif pubsub_backend == "local":
            pubsub_config["url"] = "local://"
        print_status(f"Notification backend selected: {pubsub_backend}")
        logger.info(f"User selected notification backend: {pubsub_config}")

        # --- Checkpoint Backend Setup ---
        print_status("\n--- Checkpoint Backend Setup (for state persistence) ---", "ok")
        checkpoint_backend_options = ["fs"]
        if CHECKPOINT_AVAILABLE:
            try:
                additional = list(getattr(CheckpointManager, "_BACKENDS", {}).keys())
                checkpoint_backend_options = sorted(
                    list(
                        set(
                            checkpoint_backend_options
                            + [b for b in additional if b != "fs"]
                        )
                    )
                )
            except Exception as e:
                logger.warning(
                    f"Could not get supported backends from CheckpointManager: {e}. Falling back to defaults."
                )
                checkpoint_backend_options = sorted(
                    list(
                        set(
                            checkpoint_backend_options
                            + ["s3", "redis", "postgres", "gcs", "azure", "etcd"]
                        )
                    )
                )

        if args.checkpoint_backend:
            checkpoint_backend = args.checkpoint_backend
        else:
            checkpoint_backend = (
                "fs"
                if non_interactive
                else _get_user_input(
                    f"Choose your preferred checkpoint backend: {checkpoint_backend_options}",
                    options=checkpoint_backend_options,
                    default="fs",
                )
            )

        checkpoint_config = {"type": checkpoint_backend}
        if checkpoint_backend == "fs":
            checkpoint_config["dir"] = (
                "./checkpoints"
                if non_interactive
                else _get_user_input(
                    "Enter local directory for checkpoints", default="./checkpoints"
                )
            )
        elif checkpoint_backend == "s3":
            checkpoint_config["s3_config"] = {
                "bucket": (
                    "your-s3-checkpoint-bucket"
                    if non_interactive
                    else _get_user_input(
                        "Enter S3 Bucket Name", default="your-s3-checkpoint-bucket"
                    )
                )
            }
            secrets["AWS_ACCESS_KEY_ID"] = (
                secrets.get("AWS_ACCESS_KEY_ID")
                or os.environ.get("AWS_ACCESS_KEY_ID")
                or (
                    None
                    if non_interactive
                    else _get_user_input("Enter AWS Access Key ID")
                )
            )
            secrets["AWS_SECRET_ACCESS_KEY"] = (
                secrets.get("AWS_SECRET_ACCESS_KEY")
                or os.environ.get("AWS_SECRET_ACCESS_KEY")
                or (
                    None
                    if non_interactive
                    else _get_user_input("Enter AWS Secret Access Key", secret=True)
                )
            )
            secrets["AWS_REGION_NAME"] = (
                secrets.get("AWS_REGION_NAME")
                or os.environ.get("AWS_REGION_NAME")
                or (
                    "us-east-1"
                    if non_interactive
                    else _get_user_input("Enter AWS Region", default="us-east-1")
                )
            )
        elif checkpoint_backend == "redis":
            checkpoint_config["redis_config"] = {
                "url": (
                    "redis://localhost:6379/1"
                    if non_interactive
                    else _get_user_input(
                        "Enter Redis URL for checkpoints",
                        default="redis://localhost:6379/1",
                    )
                )
            }
        elif checkpoint_backend == "postgres":
            checkpoint_config["postgres_config"] = {
                "dsn": (
                    "postgresql://user:password@localhost:5432/database"
                    if non_interactive
                    else _get_user_input(
                        "Enter Postgres DSN",
                        default="postgresql://user:password@localhost:5432/database",
                    )
                )
            }
            print_status(
                "Ensure your Postgres database has the 'checkpoints' table created.",
                "warn",
            )
        elif checkpoint_backend == "gcs":
            checkpoint_config["gcs_config"] = {
                "bucket": (
                    "your-gcs-checkpoint-bucket"
                    if non_interactive
                    else _get_user_input(
                        "Enter GCS Bucket Name for checkpoints",
                        default="your-gcs-checkpoint-bucket",
                    )
                )
            }
            secrets["GOOGLE_APPLICATION_CREDENTIALS"] = (
                secrets.get("GOOGLE_APPLICATION_CREDENTIALS")
                or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
                or (
                    None
                    if non_interactive
                    else _get_user_input("Enter path to Google Service Account Key")
                )
            )
        elif checkpoint_backend == "azure":
            checkpoint_config["azure_config"] = {
                "connection_string": (
                    "DefaultEndpointsProtocol=..."
                    if non_interactive
                    else _get_user_input(
                        "Enter Azure Storage Connection String for checkpoints",
                        default="DefaultEndpointsProtocol=...",
                    )
                ),
                "container_name": (
                    "checkpoints"
                    if non_interactive
                    else _get_user_input(
                        "Enter Azure Blob Container Name for checkpoints",
                        default="checkpoints",
                    )
                ),
            }
        elif checkpoint_backend == "etcd":
            checkpoint_config["etcd_config"] = {
                "host": (
                    "localhost"
                    if non_interactive
                    else _get_user_input(
                        "Enter etcd Host for checkpoints", default="localhost"
                    )
                ),
                "port": (
                    "2379"
                    if non_interactive
                    else _get_user_input(
                        "Enter etcd Port for checkpoints", default="2379"
                    )
                ),
            }
        print_status(f"Checkpoint backend selected: {checkpoint_backend}")
        logger.info(f"User selected checkpoint backend: {checkpoint_config}")

        # Store secrets only if we actually have values
        if any(v for v in secrets.values()):
            _generate_secure_config(
                {k: v for k, v in secrets.items() if v}, secrets_manager
            )

        # --- Generate Core Configuration ---
        core_config = {
            "project_type": project_type,
            "plugins_dir": str(PLUGINS_DIR),
            "results_dir": str(RESULTS_DIR),
            "notification_backend": pubsub_config,
            "checkpoint_backend": checkpoint_config,
            "environment_variables": {
                "MESH_BACKEND_URL": pubsub_config.get("url", ""),
                "CHECKPOINT_BACKEND_TYPE": checkpoint_config.get("type", ""),
                "CHECKPOINT_FS_DIR": (
                    checkpoint_config.get("dir", "")
                    if checkpoint_config.get("type") == "fs"
                    else ""
                ),
            },
            "generated_with": {
                "wizard_version": "1.0.0",
                "python_version": platform.python_version(),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
        await _generate_config(core_config)
        await _generate_readme(
            CONFIG_DIR,
            "Configs Directory",
            "Contains configuration files for your project.",
        )

        # --- Generate Demo Plugin ---
        for p_type in selected_plugin_types:
            plugin_name = f"demo_{p_type}_plugin"
            await _generate_plugin_manifest(p_type, plugin_name, PLUGINS_DIR)

        await _generate_readme(
            RESULTS_DIR,
            "Simulation Results",
            "Simulation outputs and logs will be stored here.",
        )

        # --- Generate CI ---
        if non_interactive:
            _generate_ci_yaml(ci_env)
        else:
            generate_ci_opt = _get_user_input(
                "Generate GitHub Actions CI YAML?", options=["yes", "no"], default="yes"
            )
            if generate_ci_opt == "yes":
                _generate_ci_yaml(ci_env)

        # --- Optional Health Checks ---
        if non_interactive:
            # Default to no in non-interactive to avoid flaky network checks
            run_health_checks_opt = "no"
        else:
            run_health_checks_opt = _get_user_input(
                "Do you want to run health checks for the configured backends now?",
                options=["yes", "no"],
                default="yes",
            )
        if run_health_checks_opt == "yes":
            await _run_health_checks(core_config)

        # --- Run Test Simulation (best-effort) ---
        if not non_interactive:
            run_test_sim = _get_user_input(
                "Do you want to run a test simulation now?",
                options=["yes", "no"],
                default="no",
            )
            if run_test_sim == "yes":
                try:
                    subprocess.run(
                        [
                            sys.executable,
                            "main_sim_runner.py",
                            "--session",
                            "test_onboard",
                        ],
                        check=True,
                    )
                    print_status("Test simulation completed successfully.", "ok")
                except Exception as e:
                    print_status(f"Test simulation failed: {e}", "err")

        # --- Telemetry Opt-in ---
        if not non_interactive:
            telemetry_opt = _get_user_input(
                "Opt-in to anonymized telemetry for improvement? (no data sent otherwise)",
                options=["yes", "no"],
                default="no",
            )
            if telemetry_opt == "yes":
                telemetry_data = {
                    "project_type": project_type,
                    "plugin_types": selected_plugin_types,
                    "pubsub_backend": pubsub_backend,
                    "checkpoint_backend": checkpoint_backend,
                    "python_version": platform.python_version(),
                    "timestamp": time.time(),
                }
                try:
                    _send_telemetry(telemetry_data)
                    print_status("Thank you for opting in to telemetry!", "ok")
                except Exception as e:
                    print_status(f"Telemetry failed to send: {e}", "warn")
                    logger.warning("Telemetry failed to send.", exc_info=True)

        # --- First Job Command Hint ---
        if not non_interactive:
            run_first_job_opt = _get_user_input(
                "Do you want to see the command to run your first job?",
                options=["yes", "no"],
                default="yes",
            )
            if run_first_job_opt == "yes":
                print_status("\n--- Command to Run Your First Job ---", "ok")
                print_status(
                    "To run a simulation using your new configuration and demo plugin, run:",
                    "info",
                )
                print_status(
                    f"python your_main_simulation_script.py --config {CONFIG_DIR / 'config.json'}",
                    "info",
                )
                print_status(
                    "Make sure your main script initializes MeshPubSub and CheckpointManager with values from config.json.",
                    "info",
                )
                print_status(
                    f"Example: python -m your_app.main --config {CONFIG_DIR / 'config.json'}",
                    "info",
                )
                print_status("Or, if using environment variables:", "info")
                print_status(
                    f"export MESH_BACKEND_URL='{pubsub_config.get('url', '')}'", "info"
                )
                print_status(
                    f"export CHECKPOINT_BACKEND_TYPE='{checkpoint_config.get('type', '')}'",
                    "info",
                )
                if checkpoint_config.get("type") == "fs":
                    print_status(
                        f"export CHECKPOINT_FS_DIR='{checkpoint_config.get('dir', '')}'",
                        "info",
                    )
                print_status("python your_main_simulation_script.py", "info")

        _print_security_checklist(core_config)
        if not non_interactive and args.show_examples:
            _show_examples()
        _auto_open_docs()
        _print_support_links()

        ONBOARDING_STEPS_TOTAL.labels(step="complete").inc()
        print_status("\n--- Onboarding Complete! ---", "ok")
        print_status(
            "You're all set. Explore the generated files and start building!", "ok"
        )
    except Exception as e:
        print_status(f"Onboarding failed: {e}", "err")
        _cleanup_partial_onboard()
        ONBOARDING_ERRORS_TOTAL.labels(error_type="onboarding_failure").inc()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--safe",
        action="store_true",
        help="Run onboarding in SAFE MODE / Starter Profile (local demo only)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset configs/plugins to safe mode / starter profile",
    )
    parser.add_argument(
        "--troubleshoot",
        action="store_true",
        help="Run health checks and onboarding diagnostics",
    )
    parser.add_argument(
        "--show-examples",
        action="store_true",
        help="Show example configs, plugins, jobs after onboarding",
    )
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress non-essential output"
    )
    parser.add_argument(
        "--json-log", action="store_true", help="Enable structured JSON logging"
    )
    parser.add_argument("--project-type", type=str, help="Set project type (no prompt)")
    parser.add_argument(
        "--plugin-types", type=str, help="Set plugin types (comma-separated, no prompt)"
    )
    parser.add_argument(
        "--pubsub-backend", type=str, help="Set pubsub backend (no prompt)"
    )
    parser.add_argument(
        "--checkpoint-backend", type=str, help="Set checkpoint backend (no prompt)"
    )
    parser.add_argument(
        "-h", "--help", action="store_true", help="Show this help message"
    )
    args = parser.parse_args()

    setup_logging(args.verbose, args.quiet, args.json_log)

    if args.help:
        _print_help()
        sys.exit(0)

    try:
        if args.reset:
            asyncio.run(_reset_to_safe_mode())
            sys.exit(0)
        elif args.safe:
            asyncio.run(_safe_mode_profile())
            sys.exit(0)
        elif args.troubleshoot:
            try:
                with open(CONFIG_DIR / "config.json", "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                asyncio.run(_run_health_checks(config_data))
            except FileNotFoundError:
                print_status(
                    "No config.json found. Please run onboarding first or use --safe.",
                    "err",
                )
            _run_basic_onboarding_tests()
            sys.exit(0)

        asyncio.run(onboard(args))
        _run_basic_onboarding_tests()
    except Exception as e:
        print_status(f"Onboarding failed with error: {e}", "err")
        logger.critical(f"Onboarding failed with error: {e}", exc_info=True)
        sys.exit(1)
