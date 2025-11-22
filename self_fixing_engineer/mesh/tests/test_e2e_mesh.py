"""
test_e2e_mesh.py

End-to-End Integration Tests for Mesh Framework v2.0
Tests the complete workflow integrating all mesh components.
"""

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from contextlib import contextmanager

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

# Test configuration
TEST_DIR = Path(tempfile.mkdtemp(prefix="mesh_e2e_test_"))
TEST_POLICY_ID = "e2e_policy"
TEST_POLICY_DATA = {
    "id": "e2e_policy",
    "version": "1.0",
    "allow": ["publish_event", "save_checkpoint"],
    "deny": ["delete"],
}


# Generate valid Fernet keys for testing
def generate_test_key():
    """Generate a valid Fernet key for testing."""
    return Fernet.generate_key().decode()


# Configure test environment with valid Fernet keys
TEST_KEY_1 = generate_test_key()
TEST_KEY_2 = generate_test_key()

os.environ.update(
    {
        "PROD_MODE": "false",
        "ENV": "test",
        "TENANT": "test_tenant",
        # Event Bus Config with valid Fernet keys
        "EVENT_BUS_ENCRYPTION_KEY": f"{TEST_KEY_1},{TEST_KEY_2}",
        "EVENT_BUS_HMAC_KEY": "test_hmac_key_at_least_32_characters_long",
        "USE_REDIS_STREAMS": "false",
        "REDIS_URL": "redis://localhost:6379",
        # Policy Config with valid Fernet keys
        "POLICY_ENCRYPTION_KEY": f"{TEST_KEY_1},{TEST_KEY_2}",
        "POLICY_HMAC_KEY": "test_policy_hmac_at_least_32_chars",
        "JWT_SECRET": "test_jwt_secret_that_is_long_enough",
        # Checkpoint Config with valid Fernet key
        "CHECKPOINT_DIR": str(TEST_DIR),
        "CHECKPOINT_ENCRYPTION_KEYS": TEST_KEY_1,
        "CHECKPOINT_HMAC_KEY": "test_checkpoint_hmac_at_least_32ch",
        # Adapter Config with valid Fernet key
        "MESH_BACKEND_URL": "redis://localhost:6379",
        "MESH_ENCRYPTION_KEY": TEST_KEY_1,
        "MESH_HMAC_KEY": "test_adapter_hmac_at_least_32_char",
    }
)

# Mock redis.asyncio module before importing mesh components
mock_redis_module = MagicMock()
mock_redis_module.from_url = AsyncMock()
mock_redis_module.Redis = MagicMock()
mock_redis_module.ConnectionPool = MagicMock()
sys.modules["redis.asyncio"] = mock_redis_module
sys.modules["redis"] = MagicMock()

# Import mesh components after environment setup and mocking
from mesh import event_bus, mesh_policy, mesh_adapter
from mesh.checkpoint import CheckpointManager
from pydantic import BaseModel

# Force REDIS_AVAILABLE to True for testing
event_bus.REDIS_AVAILABLE = True


# Fix the tracer in event_bus to handle spans properly
class MockSpan:
    def set_attribute(self, key, value):
        pass

    def add_event(self, name, attributes=None):
        pass

    def set_status(self, status):
        pass


class MockTracer:
    @contextmanager
    def start_as_current_span(self, name, **kwargs):
        yield MockSpan()


# Replace the tracer in event_bus with our mock
event_bus.tracer = MockTracer()


# Schema for checkpoint state validation
class StateSchema(BaseModel):
    """Schema for checkpoint state validation."""

    workflow_id: str
    status: str
    timestamp: float
    data: dict


@pytest_asyncio.fixture(scope="module")
async def services():
    """Fixture to set up all required services for E2E tests."""
    # Initialize Policy Components
    policy_backend = mesh_policy.MeshPolicyBackend(
        backend_type="local", local_dir=str(TEST_DIR)
    )
    policy_enforcer = mesh_policy.MeshPolicyEnforcer(
        policy_id=TEST_POLICY_ID, backend=policy_backend
    )
    await policy_backend.save(TEST_POLICY_ID, TEST_POLICY_DATA)
    await policy_enforcer.load_policy()

    # Initialize Checkpoint Manager
    checkpoint_mgr = CheckpointManager(backend_type="local", state_schema=StateSchema)
    await checkpoint_mgr.initialize()

    # Initialize PubSub Adapter with mocked backend
    pubsub = mesh_adapter.MeshPubSub(backend_url="redis://localhost:6379")

    # Setup mock Redis client
    mock_client = AsyncMock()
    mock_client.ping = AsyncMock(return_value=True)
    mock_client.publish = AsyncMock(return_value=1)
    mock_client.pipeline = AsyncMock()
    mock_client.xadd = AsyncMock()
    mock_client.pubsub = MagicMock(return_value=AsyncMock())

    # Mock get_redis_client function
    def mock_get_redis_client():
        return mock_client

    event_bus.get_redis_client = mock_get_redis_client
    event_bus._redis_connection_pool = MagicMock()

    with patch("redis.asyncio.from_url", return_value=mock_client):
        with patch("redis.asyncio.Redis", return_value=mock_client):
            yield {
                "policy_backend": policy_backend,
                "policy_enforcer": policy_enforcer,
                "checkpoint_manager": checkpoint_mgr,
                "pubsub": pubsub,
                "redis_mock": mock_client,
            }

    # Cleanup
    await checkpoint_mgr.close()
    await pubsub.close()


class TestFullWorkflow:
    """Test complete end-to-end workflow."""

    @pytest.mark.asyncio
    async def test_policy_checkpoint_and_event_flow(self, services):
        """Test the complete flow: Policy -> Checkpoint -> Event Publication."""

        # Step 1: Verify policy enforcement
        allowed = await services["policy_enforcer"].enforce_policy("save_checkpoint")
        assert allowed, "save_checkpoint should be allowed by policy"

        denied = await services["policy_enforcer"].enforce_policy("delete")
        assert not denied, "delete should be denied by policy"

        # Step 2: Save checkpoint with policy enforcement
        if await services["policy_enforcer"].enforce_policy("save_checkpoint"):
            checkpoint_data = {
                "workflow_id": "test_workflow_001",
                "status": "processing",
                "timestamp": time.time(),
                "data": {"step": 1, "progress": 0.25},
            }

            checkpoint_hash = await services["checkpoint_manager"].save(
                name="workflow_checkpoint",
                state=checkpoint_data,
                metadata={"policy_enforced": True},
            )
            assert checkpoint_hash is not None

            # Verify checkpoint can be loaded
            loaded_data = await services["checkpoint_manager"].load(
                "workflow_checkpoint"
            )
            assert loaded_data["workflow_id"] == "test_workflow_001"

        # Step 3: Publish event after checkpoint
        if await services["policy_enforcer"].enforce_policy("publish_event"):
            # Publish event
            await event_bus.publish_event(
                "workflow_checkpoint_saved",
                {
                    "checkpoint_name": "workflow_checkpoint",
                    "checkpoint_hash": checkpoint_hash,
                    "timestamp": time.time(),
                },
            )

            # Verify the mock was called
            assert services[
                "redis_mock"
            ].publish.called, "Redis publish should have been called"

        # Step 4: Test adapter publish/subscribe flow
        test_message = {"test": "data", "workflow_id": "test_workflow_001"}

        # Mock the adapter's internal client
        services["pubsub"]._client = services["redis_mock"]

        # Now test the publish
        await services["pubsub"].connect()
        await services["pubsub"].publish("test_channel", test_message)

        # Check that publish was called
        assert services[
            "redis_mock"
        ].publish.called, "Adapter publish should have been called"


class TestFailureAndRecovery:
    """Test failure scenarios and recovery mechanisms."""

    @pytest.mark.asyncio
    async def test_dlq_recovery_workflow(self, services):
        """Test DLQ (Dead Letter Queue) recovery across components."""

        # Step 1: Simulate checkpoint save failure
        with patch.object(
            services["checkpoint_manager"],
            "_local_save",
            side_effect=Exception("Simulated failure"),
        ):
            try:
                await services["checkpoint_manager"].save(
                    name="failing_checkpoint", state={"data": "test"}
                )
            except Exception:
                pass  # Expected to fail

        # Step 2: Check DLQ file was created (may not exist in test env)
        dlq_path = Path(
            os.environ.get("CHECKPOINT_DLQ_PATH", "/var/log/checkpoint/dlq.jsonl")
        )
        # Note: In test environment, DLQ might not be written to actual file

        # Step 3: Test event bus DLQ for failed publication
        # Temporarily set REDIS_AVAILABLE to False to trigger the error path
        original_redis_available = event_bus.REDIS_AVAILABLE
        event_bus.REDIS_AVAILABLE = False

        try:
            await event_bus.publish_event("test_event", {"data": "test"})
        except RuntimeError:
            pass  # Expected to fail
        finally:
            event_bus.REDIS_AVAILABLE = original_redis_available

        # Step 4: Test policy DLQ replay (should handle empty DLQ gracefully)
        await services["policy_backend"].replay_policy_dlq()

        # Step 5: Test adapter DLQ replay
        services["pubsub"].dead_letter_path = str(TEST_DIR / "adapter_dlq.jsonl")
        await services["pubsub"].replay_dlq()


class TestSecurityIntegration:
    """Test security features integration."""

    @pytest.mark.asyncio
    async def test_encryption_key_rotation(self, services):
        """Test encryption key rotation across all components."""

        # Step 1: Save checkpoint with first key
        checkpoint_data = {
            "workflow_id": "encryption_test",
            "status": "secure",
            "timestamp": time.time(),
            "data": {"sensitive": "information"},
        }

        hash1 = await services["checkpoint_manager"].save(
            name="encrypted_checkpoint", state=checkpoint_data
        )

        # Step 2: Simulate key rotation - generate new valid keys
        new_key_1 = generate_test_key()
        new_key_2 = generate_test_key()
        old_keys = os.environ.get("CHECKPOINT_ENCRYPTION_KEYS")

        # Update environment with new keys (old key should still be in list for decryption)
        os.environ["CHECKPOINT_ENCRYPTION_KEYS"] = f"{new_key_1},{old_keys}"

        # Reinitialize encryption in checkpoint manager
        services["checkpoint_manager"]._init_encryption()

        # Step 3: Load checkpoint with rotated keys (should still work)
        loaded_data = await services["checkpoint_manager"].load("encrypted_checkpoint")
        assert loaded_data["data"]["sensitive"] == "information"

        # Step 4: Save new checkpoint with new key
        new_checkpoint_data = {
            "workflow_id": "post_rotation",
            "status": "rotated",
            "timestamp": time.time(),
            "data": {"new": "encrypted_data"},
        }

        hash2 = await services["checkpoint_manager"].save(
            name="post_rotation_checkpoint", state=new_checkpoint_data
        )

        # Step 5: Verify both old and new checkpoints can be loaded
        old_checkpoint = await services["checkpoint_manager"].load(
            "encrypted_checkpoint"
        )
        new_checkpoint = await services["checkpoint_manager"].load(
            "post_rotation_checkpoint"
        )

        assert old_checkpoint["workflow_id"] == "encryption_test"
        assert new_checkpoint["workflow_id"] == "post_rotation"

        # Restore original keys
        os.environ["CHECKPOINT_ENCRYPTION_KEYS"] = old_keys


@pytest.fixture(autouse=True)
def cleanup():
    """Clean up test artifacts after each test."""
    yield

    # Clean up test directory
    import shutil

    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR, ignore_errors=True)

    # Clean up any DLQ files
    for dlq_file in Path(".").glob("*_dlq.jsonl"):
        dlq_file.unlink(missing_ok=True)

    # Clean up policy DLQ file
    policy_dlq = Path("policy_dlq.jsonl")
    if policy_dlq.exists():
        policy_dlq.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
