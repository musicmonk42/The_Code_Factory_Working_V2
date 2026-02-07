# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# tests/test_onboard.py

import argparse
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the onboard module from the correct path
import self_fixing_engineer.simulation.plugins.onboard as onboard_module
from cryptography.fernet import Fernet
from self_fixing_engineer.simulation.plugins.onboard import (
    _generate_secure_config,
    _get_user_input,
    _load_secure_config,
    _reset_to_safe_mode,
    _run_basic_onboarding_tests,
    _run_health_checks,
    _safe_mode_profile,
    onboard,
)

# ==============================================================================
# Pytest Fixtures for mocking external dependencies and environment
# ==============================================================================


@pytest.fixture(autouse=True)
def mock_external_dependencies():
    """
    Mocks external libraries, environment variables, and filesystem for isolation.
    """
    # Create mocks for the conditional imports
    mock_mesh_pubsub = MagicMock()
    mock_mesh_pubsub.return_value.connect = AsyncMock()
    mock_mesh_pubsub.return_value.close = AsyncMock()
    mock_mesh_pubsub.return_value.healthcheck = AsyncMock(
        return_value={"status": "ok", "message": "Mocked health check"}
    )
    mock_mesh_pubsub.supported_backends = MagicMock(return_value=["redis"])

    mock_checkpoint_manager = MagicMock()
    mock_checkpoint_manager.return_value.save = AsyncMock()
    mock_checkpoint_manager.return_value.load = AsyncMock(
        return_value={"status": "healthy"}
    )
    mock_checkpoint_manager.return_value.delete = AsyncMock()
    mock_checkpoint_manager._BACKENDS = {"fs": None}

    # Set these attributes on the module if they don't exist
    if not hasattr(onboard_module, "MeshPubSub"):
        onboard_module.MeshPubSub = mock_mesh_pubsub
    else:
        # If it exists, save the original to restore later
        getattr(onboard_module, "MeshPubSub", None)
        onboard_module.MeshPubSub = mock_mesh_pubsub

    if not hasattr(onboard_module, "CheckpointManager"):
        onboard_module.CheckpointManager = mock_checkpoint_manager
    else:
        getattr(onboard_module, "CheckpointManager", None)
        onboard_module.CheckpointManager = mock_checkpoint_manager

    # Create patches list that we'll apply
    patches = [
        # Don't mock os.makedirs - we need real directories to be created
        patch("self_fixing_engineer.simulation.plugins.onboard.MESH_ADAPTER_AVAILABLE", True),
        patch("self_fixing_engineer.simulation.plugins.onboard.CHECKPOINT_AVAILABLE", True),
        patch("self_fixing_engineer.simulation.plugins.onboard.vault_available", True),
        patch("self_fixing_engineer.simulation.plugins.onboard.crypto_available", True),
        patch("self_fixing_engineer.simulation.plugins.onboard.prometheus_available", True),
        patch("self_fixing_engineer.simulation.plugins.onboard.tenacity_available", True),
        patch(
            "self_fixing_engineer.simulation.plugins.onboard.aiofiles_available", False
        ),  # Use sync file operations
        patch("self_fixing_engineer.simulation.plugins.onboard.requests.post"),
        (
            patch("self_fixing_engineer.simulation.plugins.onboard.hvac.Client")
            if hasattr(onboard_module, "hvac")
            else None
        ),
        patch("self_fixing_engineer.simulation.plugins.onboard.subprocess.run"),
        patch("self_fixing_engineer.simulation.plugins.onboard.webbrowser.open"),
    ]

    # Filter out None patches
    patches = [p for p in patches if p is not None]

    # Start all patches
    started_patches = []
    for p in patches:
        started_patches.append(p.start())

    # Configure the mocks
    mock_requests_post = started_patches[7] if len(started_patches) > 7 else MagicMock()
    mock_requests_post.return_value.raise_for_status = MagicMock()

    mock_hvac_client = started_patches[8] if len(started_patches) > 8 else MagicMock()
    mock_subprocess_run = (
        started_patches[9] if len(started_patches) > 9 else MagicMock()
    )
    mock_webbrowser_open = (
        started_patches[10] if len(started_patches) > 10 else MagicMock()
    )

    yield {
        "mock_requests_post": mock_requests_post,
        "mock_hvac_client": mock_hvac_client,
        "MockMeshPubSub": mock_mesh_pubsub,
        "MockCheckpointManager": mock_checkpoint_manager,
        "mock_subprocess_run": mock_subprocess_run,
        "mock_webbrowser_open": mock_webbrowser_open,
    }

    # Stop all patches
    for p in patches:
        if p:
            p.stop()


@pytest.fixture
def mock_filesystem():
    """Mocks the filesystem for file-related operations."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create all required directories
        configs_dir = temp_path / "configs"
        plugins_dir = temp_path / "plugins"
        results_dir = temp_path / "results"
        ci_dir = temp_path / ".github" / "workflows"

        os.makedirs(configs_dir, exist_ok=True)
        os.makedirs(plugins_dir, exist_ok=True)
        os.makedirs(results_dir, exist_ok=True)
        os.makedirs(ci_dir, exist_ok=True)

        with (
            patch("self_fixing_engineer.simulation.plugins.onboard.script_dir", temp_path),
            patch("self_fixing_engineer.simulation.plugins.onboard.CONFIG_DIR", configs_dir),
            patch("self_fixing_engineer.simulation.plugins.onboard.PLUGINS_DIR", plugins_dir),
            patch("self_fixing_engineer.simulation.plugins.onboard.RESULTS_DIR", results_dir),
            patch("self_fixing_engineer.simulation.plugins.onboard.CI_DIR", ci_dir),
            patch(
                "self_fixing_engineer.simulation.plugins.onboard.SECURE_CONFIG_PATH",
                configs_dir / "secure.json",
            ),
            patch(
                "self_fixing_engineer.simulation.plugins.onboard.SECURE_KEY_PATH", configs_dir / "secure.key"
            ),
        ):

            yield {
                "temp_path": temp_path,
            }


@pytest.fixture
def mock_user_input():
    """Mocks user input for the interactive CLI."""
    return [
        "agentic_swarm",  # project_type
        "python",  # plugin_types
        "redis",  # pubsub_backend
        "redis://localhost:6379",  # Redis URL
        "fs",  # checkpoint_backend
        "./checkpoints",  # FS dir
        "yes",  # generate ci
        "yes",  # run health checks
        "no",  # run test sim
        "no",  # telemetry
        "no",  # show command
    ]


# ==============================================================================
# Unit Tests for core Onboarding logic
# ==============================================================================


@pytest.mark.asyncio
async def test_onboarding_wizard_full_flow(
    mock_external_dependencies, mock_filesystem, mock_user_input
):
    """
    Test the entire interactive onboarding wizard workflow.
    """
    with (
        patch("builtins.input", side_effect=mock_user_input),
        patch("self_fixing_engineer.simulation.plugins.onboard.print_status") as mock_print_status,
        patch("self_fixing_engineer.simulation.plugins.onboard._non_interactive", return_value=False),
        patch("self_fixing_engineer.simulation.plugins.onboard._check_existing_configs"),
    ):

        args = argparse.Namespace(
            help=False,
            reset=False,
            safe=False,
            troubleshoot=False,
            show_examples=False,
            verbose=False,
            quiet=False,
            json_log=False,
            project_type=None,
            plugin_types=None,
            pubsub_backend=None,
            checkpoint_backend=None,
        )
        await onboard(args)

        # Verify that all key files were created
        assert (mock_filesystem["temp_path"] / "configs" / "config.json").exists()
        assert (
            mock_filesystem["temp_path"] / "plugins" / "demo_python_plugin.py"
        ).exists()
        assert (mock_filesystem["temp_path"] / "results" / "README.md").exists()
        assert (
            mock_filesystem["temp_path"] / ".github" / "workflows" / "ci.yaml"
        ).exists()

        # Verify health checks were called
        mock_external_dependencies["MockMeshPubSub"].assert_called()
        mock_external_dependencies["MockCheckpointManager"].assert_called()

        # Verify final status message
        mock_print_status.assert_any_call("\n--- Onboarding Complete! ---", "ok")


@pytest.mark.asyncio
async def test_safe_mode_profile_generation(
    mock_filesystem, mock_external_dependencies
):
    """Test the --safe mode to ensure local-only config is generated."""
    with patch("self_fixing_engineer.simulation.plugins.onboard.print_status"):
        await _safe_mode_profile()

    config_path = mock_filesystem["temp_path"] / "configs" / "config.json"
    assert config_path.exists()

    with open(config_path, "r") as f:
        config_data = json.load(f)

    assert config_data["project_type"] == "demo_safe_mode"
    assert config_data["notification_backend"]["url"] == "local://"
    assert config_data["checkpoint_backend"]["type"] == "fs"

    plugin_path = mock_filesystem["temp_path"] / "plugins" / "demo_python_plugin.py"
    assert plugin_path.exists()


@pytest.mark.asyncio
async def test_run_health_checks_with_failures(
    mock_external_dependencies, mock_filesystem
):
    """Test that health checks correctly report failures."""
    # Mock one health check to fail
    mock_external_dependencies["MockMeshPubSub"].return_value.healthcheck = AsyncMock(
        return_value={"status": "error", "message": "Mocked failure"}
    )

    mock_config = {
        "notification_backend": {"type": "redis", "url": "redis://localhost:6379/0"},
        "checkpoint_backend": {"type": "fs", "dir": "./checkpoints"},
    }

    with patch("self_fixing_engineer.simulation.plugins.onboard.print_status") as mock_print_status:
        await _run_health_checks(mock_config)

        # Verify that an error message was printed for the failed check
        mock_print_status.assert_any_call(
            "Pub/Sub Health: ERROR - Mocked failure", "err"
        )
        mock_print_status.assert_any_call(
            "Checkpoint Health: OK (saved and loaded test data successfully for fs).",
            "ok",
        )


@pytest.mark.asyncio
async def test_reset_to_safe_mode(mock_filesystem, mock_external_dependencies):
    """Test the reset functionality."""
    # Create some dummy files to be cleaned
    dummy_config = mock_filesystem["temp_path"] / "configs" / "dummy.json"
    dummy_config.write_text("{}")

    with (
        patch("self_fixing_engineer.simulation.plugins.onboard.print_status"),
        patch("self_fixing_engineer.simulation.plugins.onboard._non_interactive", return_value=True),
    ):
        await _reset_to_safe_mode()

    # Verify dummy file was removed
    assert not dummy_config.exists()

    # Verify safe mode config was created
    config_path = mock_filesystem["temp_path"] / "configs" / "config.json"
    assert config_path.exists()


# ==============================================================================
# Unit Tests for Security and Resilience features
# ==============================================================================


def test_generate_secure_config_local_encrypted(
    mock_filesystem, mock_external_dependencies
):
    """Test local secret encryption and storage."""
    with (
        patch("self_fixing_engineer.simulation.plugins.onboard.print_status"),
        patch("self_fixing_engineer.simulation.plugins.onboard.crypto_available", True),
        patch("self_fixing_engineer.simulation.plugins.onboard.Fernet") as mock_fernet,
        patch("self_fixing_engineer.simulation.plugins.onboard._read_or_create_key") as mock_read_key,
    ):

        # Mock Fernet to return a predictable encrypted value
        mock_fernet_instance = MagicMock()
        mock_fernet_instance.encrypt.return_value = b"encrypted_value"
        mock_fernet.return_value = mock_fernet_instance
        mock_fernet.generate_key.return_value = b"test_key"
        mock_read_key.return_value = b"test_key"  # Return a valid key

        secrets = {"API_KEY": "super-secret"}
        _generate_secure_config(secrets, "local")

        secure_file_path = mock_filesystem["temp_path"] / "configs" / "secure.json"
        assert secure_file_path.exists()

        with open(secure_file_path, "r") as f:
            data = json.load(f)

        assert "secrets" in data
        assert "API_KEY" in data["secrets"]
        # The value should be encrypted
        assert data["secrets"]["API_KEY"] == "encrypted_value"


def test_load_secure_config_local_decrypted(
    mock_filesystem, mock_external_dependencies
):
    """Test local secret decryption and loading."""
    # First, generate an encrypted file
    key = Fernet.generate_key()
    fernet = Fernet(key)
    encrypted_secret = fernet.encrypt(b"my-test-secret")

    secure_data = {"secrets": {"TEST_SECRET": encrypted_secret.decode("utf-8")}}

    # Get the paths from the fixture
    configs_dir = mock_filesystem["temp_path"] / "configs"
    secure_file_path = configs_dir / "secure.json"
    secure_key_path = configs_dir / "secure.key"

    # Ensure directory exists
    os.makedirs(configs_dir, exist_ok=True)

    with open(secure_file_path, "w") as f:
        json.dump(secure_data, f)

    with open(secure_key_path, "wb") as f:
        f.write(key)

    with (
        patch("self_fixing_engineer.simulation.plugins.onboard.crypto_available", True),
        patch("self_fixing_engineer.simulation.plugins.onboard.print_status"),
    ):
        loaded_secrets = _load_secure_config()
        assert loaded_secrets["TEST_SECRET"] == "my-test-secret"


def test_run_basic_onboarding_tests(mock_filesystem, mock_external_dependencies):
    """Test the basic onboarding tests function."""
    # Get the paths from the fixture
    configs_dir = mock_filesystem["temp_path"] / "configs"
    plugins_dir = mock_filesystem["temp_path"] / "plugins"

    # Ensure directories exist
    os.makedirs(configs_dir, exist_ok=True)
    os.makedirs(plugins_dir, exist_ok=True)

    # Create test config
    config_data = {
        "project_type": "test",
        "notification_backend": {"type": "local"},
        "checkpoint_backend": {"type": "fs"},
    }
    config_path = configs_dir / "config.json"
    with open(config_path, "w") as f:
        json.dump(config_data, f)

    # Create test plugin
    plugin_content = """
PLUGIN_MANIFEST = {"name": "test"}
def plugin_health():
    return {"status": "ok"}
"""
    plugin_path = plugins_dir / "demo_python_plugin.py"
    with open(plugin_path, "w") as f:
        f.write(plugin_content)

    with patch("self_fixing_engineer.simulation.plugins.onboard.print_status") as mock_print_status:
        _run_basic_onboarding_tests()

        # Check that tests passed
        mock_print_status.assert_any_call(
            "Test 1 (Config Content): Basic fields present. PASSED.", "ok"
        )
        mock_print_status.assert_any_call(
            "Test 2 (Python Plugin Content): Basic structure present. PASSED.", "ok"
        )


def test_get_user_input_non_interactive():
    """Test that non-interactive mode returns defaults."""
    with patch("self_fixing_engineer.simulation.plugins.onboard._non_interactive", return_value=True):
        result = _get_user_input("Test prompt", default="default_value")
        assert result == "default_value"

        # Test that it raises when no default is provided
        with pytest.raises(RuntimeError, match="Non-interactive mode"):
            _get_user_input("Test prompt")
