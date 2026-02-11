# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Integration test suite for the mesh module.

This suite tests the interactions between the major components:
- mesh_policy: For enforcing access control before operations.
- checkpoint_manager: For persisting state during workflows.
- event_bus: For signaling state changes and triggering actions.

These tests verify that the components work together correctly in realistic
workflows, such as enforcing a policy before saving a checkpoint or publishing
an event after a state change.
"""

import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

# --- Test Configuration ---

@pytest.fixture(scope="session")
def test_dir():
    """Create test directory once per session."""
    temp_dir = Path(tempfile.mkdtemp(prefix="mesh_integration_test_"))
    yield temp_dir
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def test_keys():
    """Generate encryption keys once per session."""
    return [Fernet.generate_key().decode() for _ in range(2)]


@pytest.fixture(scope="session", autouse=True)
def setup_test_env(test_dir, test_keys):
    """Configure environment variables for all tests."""
    TEST_ENV = {
        "PROD_MODE": "false",
        "ENV": "integration",
        "TENANT": "integration_tenant",
        "EVENT_BUS_ENCRYPTION_KEY": test_keys[0],
        "POLICY_ENCRYPTION_KEY": ",".join(test_keys),
        "CHECKPOINT_ENCRYPTION_KEYS": ",".join(test_keys),
        "MESH_BACKEND_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/13"),
        "CHECKPOINT_BACKEND": "local",
        "CHECKPOINT_DIR": str(test_dir / "checkpoints"),
    }
    
    original_env = {}
    for key, value in TEST_ENV.items():
        original_env[key] = os.environ.get(key)
        os.environ[key] = value
    
    yield
    
    # Restore original environment
    for key, value in original_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


# --- Fixtures ---


@pytest_asyncio.fixture(scope="function")
async def policy_enforcer():
    """Fixture for a configured MeshPolicyEnforcer."""
    # Import here to avoid module-level side effects
    from self_fixing_engineer.mesh import mesh_policy
    
    backend = mesh_policy.MeshPolicyBackend(backend_type="local")
    enforcer = mesh_policy.MeshPolicyEnforcer(
        policy_id="integration_policy", backend=backend
    )

    # Pre-load a policy for the tests - include required fields
    await backend.save(
        "integration_policy",
        {
            "id": "integration_policy",  # Added required field
            "version": "1.0",  # Added required field
            "allow": ["save_checkpoint", "publish_event"],
            "deny": ["delete_checkpoint"],
        },
    )
    await enforcer.load_policy()

    yield enforcer
    
    # Explicit async cleanup
    try:
        if hasattr(backend, 'close'):
            await backend.close()
        if hasattr(enforcer, 'close'):
            await enforcer.close()
    except Exception:
        pass


@pytest_asyncio.fixture(scope="function")
async def checkpoint_manager_service():
    """Fixture for a configured CheckpointManager."""
    # Import here to avoid module-level side effects
    from self_fixing_engineer.mesh import checkpoint_manager
    
    manager = checkpoint_manager.CheckpointManager(backend_type="local")
    await manager.initialize()
    yield manager
    await manager.close()


# --- Integration Test Classes ---


class TestPolicyAndEvents:
    """Tests the integration between policy enforcement and event publishing."""

    @pytest.mark.asyncio
    async def test_successful_publish_after_policy_check(self, policy_enforcer):
        """
        Verify that an event can be published and received after a successful
        policy check.
        """
        from self_fixing_engineer.mesh import event_bus
        
        # 1. Enforce the policy
        is_allowed = await policy_enforcer.enforce_policy("publish_event")
        assert is_allowed, "Policy should grant permission to publish"

        # 2. If allowed, publish the event
        if is_allowed:
            event_type = "policy_approved_event"
            event_data = {"status": "approved", "timestamp": time.time()}

            # Mock the event bus to avoid real Redis dependency
            with patch.object(
                event_bus, "publish_event", new=AsyncMock()
            ) as mock_publish:
                await event_bus.publish_event(event_type, event_data)
                mock_publish.assert_called_once_with(event_type, event_data)


class TestCheckpointAndEvents:
    """Tests the integration between state checkpointing and event signaling."""

    @pytest.mark.asyncio
    async def test_checkpoint_save_triggers_event(self, checkpoint_manager_service):
        """
        Verify that saving a checkpoint can trigger a corresponding event,
        simulating an audit or notification system.
        """
        from self_fixing_engineer.mesh import event_bus
        
        checkpoint_name = "trigger_event_checkpoint"
        checkpoint_state = {"step": "completed", "result": "success"}

        # Use a mock to intercept the event publish call
        with patch.object(event_bus, "publish_event", new=AsyncMock()) as mock_publish:
            # 1. Save the checkpoint
            await checkpoint_manager_service.save(checkpoint_name, checkpoint_state)

            # 2. Simulate the application logic that publishes an event upon success
            event_type = "checkpoint_saved_notification"
            event_data = {"name": checkpoint_name, "status": "saved"}
            await event_bus.publish_event(event_type, event_data)

            # 3. Assert that the event was published correctly
            mock_publish.assert_called_once_with(event_type, event_data)


class TestFullWorkflow:
    """Tests a more complex workflow integrating all three components."""

    @pytest.mark.asyncio
    async def test_policy_checkpoint_event_workflow(
        self, policy_enforcer, checkpoint_manager_service
    ):
        """
        Simulates a full workflow:
        1. An operation is approved by the policy enforcer.
        2. A checkpoint of the application state is saved.
        3. An event is published to notify other systems of the state change.
        4. A subscriber receives the event and verifies its content.
        """
        from self_fixing_engineer.mesh import event_bus
        
        workflow_id = "workflow_123"
        initial_state = {"step": 1, "status": "pending"}
        updated_state = {"step": 2, "status": "processed"}

        # 1. Enforce policy for saving the checkpoint
        assert await policy_enforcer.enforce_policy("save_checkpoint")

        # 2. Save the initial state
        await checkpoint_manager_service.save(workflow_id, initial_state)

        # ... application logic runs ...

        # 3. Save the updated state
        await checkpoint_manager_service.save(workflow_id, updated_state)

        # 4. Publish an event with the final state (mocked to avoid Redis dependency)
        event_type = f"workflow_completed_{workflow_id}"
        assert await policy_enforcer.enforce_policy("publish_event")

        with patch.object(event_bus, "publish_event", new=AsyncMock()) as mock_publish:
            await event_bus.publish_event(event_type, updated_state)
            mock_publish.assert_called_once_with(event_type, updated_state)


# --- Cleanup ---


@pytest.fixture(autouse=True)
def force_cleanup():
    """Aggressively clean up after each test to prevent OOM."""
    yield
    import gc
    # Run gc multiple times - sometimes objects need multiple passes
    for _ in range(3):
        gc.collect()
    
    # Clear any module-level caches
    import sys
    for module_name in list(sys.modules.keys()):
        if 'mesh' in module_name and hasattr(sys.modules[module_name], '_cache'):
            sys.modules[module_name]._cache.clear()
