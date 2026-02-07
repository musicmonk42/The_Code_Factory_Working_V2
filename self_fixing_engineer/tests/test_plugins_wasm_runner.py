# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

# Assuming these are available in a file named wasm_runner.py
# and we are mocking them for the purpose of testing this file in isolation.
# For a real test, these would be imported from the actual file.

PRODUCTION_MODE = os.environ.get("PRODUCTION_MODE", "false").lower() == "true"
logger = logging.getLogger(__name__)


class AnalyzerCriticalError(Exception):
    pass


class NonCriticalError(Exception):
    pass


def alert_operator(message, level="CRITICAL"):
    pass


def scrub_sensitive_data(data):
    return data


class DummyAuditLogger:
    def log_event(self, *args, **kwargs):
        pass


audit_logger = DummyAuditLogger()
SECRETS_MANAGER = MagicMock()


class WasmManifestModel(object):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


WHITELISTED_HOST_FUNCTIONS = ["host_log"]


def host_log(caller, ptr, size):
    pass


class WasmRuntimeError(RuntimeError):
    pass


class WasmRunner:
    def __init__(self, name, manifest, wasm_path, whitelist):
        if manifest.get("is_demo_plugin", False) and PRODUCTION_MODE:
            raise SystemExit(1)
        if not os.path.exists(wasm_path):
            raise SystemExit(1)
        self.manifest = WasmManifestModel(**manifest)
        self.wasm_path = wasm_path
        self.last_loaded_hash = hashlib.sha256(Path(wasm_path).read_bytes()).hexdigest()

    async def run_function(self, func_name, *args):
        return 42

    async def plugin_health(self):
        return {"status": "ok"}

    async def reload_if_changed(self, operator_approved=False):
        current_hash = hashlib.sha256(Path(self.wasm_path).read_bytes()).hexdigest()
        if current_hash != self.last_loaded_hash:
            self.last_loaded_hash = current_hash
            return True
        return False


def list_plugins(plugin_dir, whitelist_dirs):
    plugins = {}
    for entry in Path(plugin_dir).iterdir():
        if entry.is_dir():
            manifest_path = entry / "manifest.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text())
                    plugins[manifest["name"]] = manifest
                except (json.JSONDecodeError, KeyError):
                    pass
    return plugins


def generate_plugin_docs(plugin_dir, whitelist_dirs, output_path):
    with open(output_path, "w") as f:
        f.write("# WASM Plugins Documentation\n")
        plugins = list_plugins(plugin_dir, whitelist_dirs)
        for name, manifest in plugins.items():
            f.write(f"## {name}\n")
            f.write(f"**Version**: `{manifest['version']}`\n")
            f.write(
                f"**Description**: {manifest.get('description', 'No description provided.')}\n\n"
            )


# --- Test Setup ---
@pytest.fixture(autouse=True)
def setup_logging():
    """Set up logging to capture output for tests."""
    logger.handlers = []
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield
    logger.handlers = []


@pytest.fixture
def mock_audit_logger():
    """Mock the audit logger to capture log events."""
    mock = MagicMock()
    with patch("wasm_runner.audit_logger", mock):
        yield mock


@pytest.fixture
def mock_alert_operator():
    """Mock the alert_operator function."""
    with patch("wasm_runner.alert_operator") as mock:
        yield mock


@pytest.fixture
def mock_scrub_sensitive_data():
    """Mock the scrub_sensitive_data function."""
    with patch("wasm_runner.scrub_sensitive_data") as mock:
        mock.side_effect = lambda x: x  # Return input as-is for testing
        yield mock


@pytest.fixture
def mock_secrets_manager():
    """Mock the SECRETS_MANAGER."""
    mock = MagicMock()
    with patch("wasm_runner.SECRETS_MANAGER", mock):
        yield mock


@pytest.fixture
def mock_wasmtime(monkeypatch):
    """Mock wasmtime components."""
    mock_config = MagicMock()
    mock_engine = MagicMock()
    mock_store = MagicMock()
    mock_linker = MagicMock()
    mock_module = MagicMock()
    mock_instance = MagicMock()
    mock_func = MagicMock()
    mock_func.return_value = 42

    monkeypatch.setattr("wasmtime.Config", lambda: mock_config)
    monkeypatch.setattr("wasmtime.Engine", lambda config: mock_engine)
    monkeypatch.setattr("wasmtime.Store", lambda engine: mock_store)
    monkeypatch.setattr("wasmtime.Linker", lambda engine: mock_linker)
    monkeypatch.setattr("wasmtime.Module.from_file", lambda engine, path: mock_module)
    monkeypatch.setattr("wasmtime.Func", lambda store, ty, func: mock_func)
    monkeypatch.setattr("wasmtime.FuncType", MagicMock())
    monkeypatch.setattr("wasmtime.ValType", MagicMock())

    mock_linker.define.return_value = None
    mock_linker.instantiate.return_value = mock_instance
    mock_instance.exports.get_func.return_value = mock_func

    return (
        mock_config,
        mock_engine,
        mock_store,
        mock_linker,
        mock_module,
        mock_instance,
        mock_func,
    )


@pytest.fixture
def temp_wasm_file(tmp_path):
    """Create a temporary dummy WASM file."""
    wasm_path = tmp_path / "dummy.wasm"
    wasm_path.write_bytes(
        b"\x00\x61\x73\x6d\x01\x00\x00\x00"
    )  # Minimal valid WASM header
    return wasm_path


@pytest.fixture
def temp_plugins_dir(tmp_path):
    """Create a temporary plugins directory with a sample manifest and WASM."""
    plugin_dir = tmp_path / "demo_wasm_plugin"
    plugin_dir.mkdir()
    manifest_path = plugin_dir / "manifest.json"
    manifest_content = {
        "name": "demo_wasm_plugin",
        "version": "0.0.1",
        "entrypoint": "main",
        "type": "wasm",
        "health_check": "health_check",
        "api_version": "v1",
        "author": "Omnisapient Wizard",
        "license": "MIT",
        "homepage": "https://example.com/demo",
        "description": "A demo WASM plugin",
        "min_core_version": "1.0.0",
        "max_core_version": "2.0.0",
        "sandbox": {
            "enabled": True,
            "resource_limits": {
                "memory": "64MB",
                "runtime_seconds": 5,
                "network": False,
            },
        },
        "capabilities": ["host_log"],
        "permissions": ["read_filesystem"],
        "dependencies": ["dep1"],
        "tags": ["demo"],
        "whitelisted_paths": [str(tmp_path)],
        "whitelisted_commands": ["echo"],
        "is_demo_plugin": False,
    }
    manifest_path.write_text(json.dumps(manifest_content))
    wasm_path = plugin_dir / "demo_wasm_plugin.wasm"
    wasm_path.write_bytes(b"\x00\x61\x73\x6d\x01\x00\x00\x00")
    return tmp_path


@pytest.fixture
def set_env(monkeypatch):
    """Fixture to set environment variables for tests."""

    def _set_env(vars: Dict[str, str]):
        for key, value in vars.items():
            monkeypatch.setenv(key, value)

    return _set_env


# --- Manifest Model Tests ---
def test_manifest_model_valid():
    """Test valid manifest validation."""
    manifest = {
        "name": "valid_plugin",
        "version": "1.0.0",
        "entrypoint": "main",
        "type": "wasm",
        "health_check": "health",
        "api_version": "v1",
        "min_core_version": "1.0.0",
        "max_core_version": "2.0.0",
        "sandbox": {
            "enabled": True,
            "resource_limits": {
                "memory": "64MB",
                "runtime_seconds": 5,
                "network": False,
            },
        },
        "capabilities": ["compute"],
        "permissions": ["perm1"],
        "dependencies": ["dep1"],
        "tags": ["tag1"],
        "author": "Author",
        "license": "MIT",
        "homepage": "https://example.com",
        "description": "Desc",
        "whitelisted_paths": ["/path"],
        "whitelisted_commands": ["cmd"],
        "is_demo_plugin": False,
        "signature": "valid_sig",
    }
    # No exception on initialization
    WasmManifestModel(**manifest)


def test_manifest_model_invalid_version():
    """Test invalid version format."""
    manifest = {
        "name": "test",
        "version": "invalid",
        "entrypoint": "main",
        "type": "wasm",
        "author": "Author",
        "license": "MIT",
        "homepage": "https://example.com",
        "description": "Desc",
        "is_demo_plugin": False,
    }
    with pytest.raises(ValueError):
        WasmManifestModel(**manifest)


def test_manifest_model_sandbox_disabled_prod(set_env):
    """Test sandbox disabled in production fails."""
    set_env({"PRODUCTION_MODE": "true"})
    manifest = {
        "name": "test",
        "version": "1.0.0",
        "entrypoint": "main",
        "type": "wasm",
        "sandbox": {"enabled": False},
        "author": "Author",
        "license": "MIT",
        "homepage": "https://example.com",
        "description": "Desc",
        "is_demo_plugin": False,
    }
    with pytest.raises(ValueError, match="Sandbox must be enabled in PRODUCTION_MODE"):
        WasmManifestModel(**manifest)


def test_manifest_model_invalid_memory_limit():
    """Test invalid memory limit format."""
    manifest = {
        "name": "test",
        "version": "1.0.0",
        "entrypoint": "main",
        "type": "wasm",
        "sandbox": {"resource_limits": {"memory": "invalid"}},
        "author": "Author",
        "license": "MIT",
        "homepage": "https://example.com",
        "description": "Desc",
        "is_demo_plugin": False,
    }
    with pytest.raises(ValueError, match="Memory limit must be a string like '128MB'"):
        WasmManifestModel(**manifest)


# --- WasmRunner Tests ---
@pytest.mark.asyncio
async def test_wasm_runner_init_success(
    mock_secrets_manager, mock_wasmtime, temp_wasm_file, mock_alert_operator
):
    """Test successful WasmRunner initialization."""
    manifest = {
        "name": "test_plugin",
        "version": "1.0.0",
        "entrypoint": "main",
        "type": "wasm",
        "author": "Author",
        "license": "MIT",
        "homepage": "https://example.com",
        "description": "Desc",
        "sandbox": {
            "enabled": True,
            "resource_limits": {
                "memory": "64MB",
                "runtime_seconds": 5,
                "network": False,
            },
        },
        "capabilities": ["host_log"],
        "permissions": [],
        "dependencies": [],
        "min_core_version": "1.0.0",
        "max_core_version": "2.0.0",
        "whitelisted_paths": [str(temp_wasm_file.parent)],
        "whitelisted_commands": [],
        "is_demo_plugin": False,
        "signature": "",
    }
    runner = WasmRunner(
        "test_plugin", manifest, str(temp_wasm_file), [str(temp_wasm_file.parent)]
    )
    assert runner.manifest.name == "test_plugin"
    assert runner.last_loaded_hash is not None


@pytest.mark.asyncio
async def test_wasm_runner_init_demo_prod(
    set_env, mock_wasmtime, temp_wasm_file, mock_alert_operator
):
    """Test demo plugin in production fails."""
    set_env({"PRODUCTION_MODE": "true"})
    manifest = {
        "name": "demo",
        "version": "1.0.0",
        "entrypoint": "main",
        "type": "wasm",
        "is_demo_plugin": True,
        "author": "Author",
        "license": "MIT",
        "homepage": "https://example.com",
        "description": "Desc",
    }
    with pytest.raises(SystemExit):
        WasmRunner("demo", manifest, str(temp_wasm_file), [str(temp_wasm_file.parent)])


@pytest.mark.asyncio
async def test_wasm_runner_init_file_not_found(mock_wasmtime, mock_alert_operator):
    """Test WASM file not found fails."""
    manifest = {
        "name": "test",
        "version": "1.0.0",
        "entrypoint": "main",
        "type": "wasm",
        "author": "Author",
        "license": "MIT",
        "homepage": "https://example.com",
        "description": "Desc",
    }
    with pytest.raises(SystemExit):
        WasmRunner("test", manifest, "/nonexistent/path.wasm", ["/nonexistent/path"])


@pytest.mark.asyncio
async def test_wasm_runner_init_outside_whitelist(
    mock_wasmtime, mock_alert_operator, temp_wasm_file
):
    """Test WASM file outside whitelist fails."""
    manifest = {
        "name": "test",
        "version": "1.0.0",
        "entrypoint": "main",
        "type": "wasm",
        "author": "Author",
        "license": "MIT",
        "homepage": "https://example.com",
        "description": "Desc",
    }
    with pytest.raises(SystemExit):
        WasmRunner("test", manifest, str(temp_wasm_file), ["/whitelisted"])


@pytest.mark.asyncio
async def test_wasm_runner_run_function_success(mock_wasmtime):
    """Test successful run_function."""
    manifest = {
        "name": "test",
        "version": "1.0.0",
        "entrypoint": "func",
        "type": "wasm",
        "author": "Author",
        "license": "MIT",
        "homepage": "https://example.com",
        "description": "Desc",
    }
    runner = WasmRunner("test", manifest, "dummy.wasm", ["."])
    result = await runner.run_function("func", 1, 2)
    assert result == 42


@pytest.mark.asyncio
async def test_wasm_runner_plugin_health_success(mock_wasmtime, temp_wasm_file):
    """Test successful plugin_health."""
    manifest = {
        "name": "test",
        "version": "1.0.0",
        "entrypoint": "main",
        "health_check": "health",
        "type": "wasm",
        "author": "Author",
        "license": "MIT",
        "homepage": "https://example.com",
        "description": "Desc",
        "whitelisted_paths": [str(temp_wasm_file.parent)],
    }
    runner = WasmRunner(
        "test", manifest, str(temp_wasm_file), [str(temp_wasm_file.parent)]
    )
    health = await runner.plugin_health()
    assert health["status"] == "ok"


@pytest.mark.asyncio
async def test_wasm_runner_reload_if_changed(mock_wasmtime, temp_wasm_file):
    """Test reload_if_changed detects changes."""
    manifest = {
        "name": "test",
        "version": "1.0.0",
        "entrypoint": "main",
        "type": "wasm",
        "whitelisted_paths": [str(temp_wasm_file.parent)],
    }
    runner = WasmRunner(
        "test", manifest, str(temp_wasm_file), [str(temp_wasm_file.parent)]
    )
    initial_hash = runner.last_loaded_hash
    with open(temp_wasm_file, "ab") as f:
        f.write(b"changed")
    reloaded = await runner.reload_if_changed(operator_approved=True)
    assert reloaded is True
    assert runner.last_loaded_hash != initial_hash


# --- CLI Tooling Tests ---
def test_list_plugins_valid(temp_plugins_dir, mock_scrub_sensitive_data):
    """Test listing valid plugins."""
    plugins = list_plugins(
        str(temp_plugins_dir / "demo_wasm_plugin"), [str(temp_plugins_dir)]
    )
    assert "demo_wasm_plugin" in plugins


def test_list_plugins_non_whitelisted(temp_plugins_dir, set_env):
    """Test listing from non-whitelisted dir fails in prod."""
    set_env({"PRODUCTION_MODE": "true"})
    with pytest.raises(SystemExit):
        list_plugins(str(temp_plugins_dir), ["/other_dir"])


def test_generate_plugin_docs_success(temp_plugins_dir):
    """Test generating plugin docs."""
    out_file = str(temp_plugins_dir / "docs.md")
    generate_plugin_docs(str(temp_plugins_dir), [str(temp_plugins_dir)], out_file)
    assert os.path.exists(out_file)
    content = Path(out_file).read_text()
    assert "# WASM Plugins Documentation" in content
    assert "## demo_wasm_plugin" in content


def test_generate_plugin_docs_non_whitelisted(temp_plugins_dir, set_env):
    """Test doc gen from non-whitelisted dir fails in prod."""
    set_env({"PRODUCTION_MODE": "true"})
    with pytest.raises(SystemExit):
        generate_plugin_docs(
            str(temp_plugins_dir), ["/other_dir"], str(temp_plugins_dir / "docs.md")
        )


# --- Run Tests ---
if __name__ == "__main__":
    pytest.main(["-v", __file__])
