# test_dlt_simple_clients.py
"""
Enterprise Production-Grade Test Suite for SimpleDLT Client
Focused on essential test coverage with high quality.
"""

import asyncio
import json
import time
import uuid
import hashlib
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

# Import the module under test
from simulation.plugins.dlt_clients.dlt_simple_clients import (
    SimpleDLTClient,
    SimpleDLTConfig,
    create_simple_dlt_client,
    PLUGIN_MANIFEST,
)

from simulation.plugins.dlt_clients.dlt_base import (
    DLTClientConfigurationError,
    DLTClientTransactionError,
    DLTClientValidationError,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_off_chain_client():
    """Creates a mock off-chain client."""
    client = AsyncMock()
    client.client_type = "MockOffChain"
    client.save_blob = AsyncMock(return_value=f"off-chain-{uuid.uuid4().hex[:8]}")
    client.get_blob = AsyncMock(return_value=b"test_payload_data")
    client.health_check = AsyncMock(return_value={
        "status": True,
        "message": "Healthy",
        "details": {}
    })
    client.close = AsyncMock()
    return client


@pytest.fixture
def config():
    """Basic SimpleDLT configuration."""
    return {
        "simpledlt": {
            "log_format": "json",
            "temp_file_ttl": 3600.0,
            "cleanup_interval": 300.0
        }
    }


@pytest.fixture
async def client(config, mock_off_chain_client):
    """Creates and initializes a SimpleDLT client."""
    client = SimpleDLTClient(config, mock_off_chain_client)
    await client.initialize()
    yield client
    await client.close()


@pytest.fixture
def sample_data():
    """Sample checkpoint data."""
    return {
        "name": f"checkpoint_{uuid.uuid4().hex[:8]}",
        "hash": hashlib.sha256(b"test").hexdigest(),
        "prev_hash": hashlib.sha256(b"prev").hexdigest(),
        "metadata": {"author": "test", "version": "1.0"},
        "payload": b"test_payload_data"
    }


# ============================================================================
# Configuration Tests
# ============================================================================

class TestConfiguration:
    """Tests for configuration validation."""

    def test_valid_config(self):
        """Test valid configuration."""
        config = SimpleDLTConfig(
            log_format="json",
            temp_file_ttl=3600.0,
            cleanup_interval=300.0
        )
        assert config.log_format == "json"
        assert config.temp_file_ttl == 3600.0

    def test_invalid_ttl(self):
        """Test invalid TTL value."""
        with pytest.raises(ValidationError) as exc:
            SimpleDLTConfig(temp_file_ttl=30.0)  # Below minimum
        assert "temp_file_ttl" in str(exc.value)

    def test_invalid_config_raises_error(self, mock_off_chain_client):
        """Test client initialization with invalid config."""
        invalid_config = {"simpledlt": {"temp_file_ttl": "invalid"}}
        with pytest.raises(DLTClientConfigurationError):
            SimpleDLTClient(invalid_config, mock_off_chain_client)

    @patch('simulation.plugins.dlt_clients.dlt_simple_clients.PRODUCTION_MODE', True)
    def test_production_requires_chain_path(self):
        """Test production mode requires chain state path."""
        with pytest.raises(ValidationError) as exc:
            SimpleDLTConfig()
        assert "chain_state_path" in str(exc.value)


# ============================================================================
# Core Operations Tests
# ============================================================================

class TestCoreOperations:
    """Tests for core DLT operations."""

    async def test_write_checkpoint(self, client, sample_data):
        """Test writing a checkpoint."""
        tx_id, off_chain_id, version = await client.write_checkpoint(
            checkpoint_name=sample_data["name"],
            hash=sample_data["hash"],
            prev_hash=sample_data["prev_hash"],
            metadata=sample_data["metadata"],
            payload_blob=sample_data["payload"],
            correlation_id="test-001"
        )

        assert tx_id.startswith(sample_data["name"])
        assert "tx1" in tx_id
        assert version == 1
        assert sample_data["name"] in client.chain
        assert len(client.chain[sample_data["name"]]) == 1

        # Verify off-chain save was called
        client.off_chain_client.save_blob.assert_called_once()

    async def test_read_checkpoint(self, client, sample_data):
        """Test reading a checkpoint."""
        # Write first
        await client.write_checkpoint(
            checkpoint_name=sample_data["name"],
            hash=sample_data["hash"],
            prev_hash=sample_data["prev_hash"],
            metadata=sample_data["metadata"],
            payload_blob=sample_data["payload"]
        )

        # Read back
        result = await client.read_checkpoint(sample_data["name"])
        
        assert result["metadata"]["hash"] == sample_data["hash"]
        assert result["metadata"]["metadata"] == sample_data["metadata"]
        assert result["payload_blob"] == b"test_payload_data"
        
        # Verify off-chain get was called
        client.off_chain_client.get_blob.assert_called_once()

    async def test_read_specific_version(self, client):
        """Test reading a specific version."""
        name = "versioned_checkpoint"
        
        # Write multiple versions
        for i in range(3):
            await client.write_checkpoint(
                checkpoint_name=name,
                hash=f"hash_{i}",
                prev_hash=f"prev_{i}",
                metadata={"version": i},
                payload_blob=f"data_{i}".encode()
            )

        # Read version 2
        result = await client.read_checkpoint(name, version=2)
        assert result["metadata"]["version"] == 2
        assert result["metadata"]["hash"] == "hash_1"

    async def test_rollback_checkpoint(self, client):
        """Test rolling back to a previous version."""
        name = "rollback_test"
        
        # Create history
        hashes = []
        for i in range(3):
            await client.write_checkpoint(
                checkpoint_name=name,
                hash=f"hash_{i}",
                prev_hash=f"prev_{i}",
                metadata={"iteration": i},
                payload_blob=f"data_{i}".encode()
            )
            hashes.append(f"hash_{i}")

        # Rollback to first version
        result = await client.rollback_checkpoint(
            name=name,
            rollback_hash=hashes[0]
        )

        assert result["version"] == 4  # New version after 3 writes
        assert result["hash"] == hashes[0]
        assert "rollback_from_hash" in result

    async def test_nonexistent_checkpoint(self, client):
        """Test reading nonexistent checkpoint."""
        with pytest.raises(FileNotFoundError):
            await client.read_checkpoint("nonexistent")

    async def test_get_version_tx(self, client, sample_data):
        """Test getting version transaction details."""
        await client.write_checkpoint(
            checkpoint_name=sample_data["name"],
            hash=sample_data["hash"],
            prev_hash=sample_data["prev_hash"],
            metadata=sample_data["metadata"],
            payload_blob=sample_data["payload"]
        )

        result = await client.get_version_tx(sample_data["name"], version=1)
        assert result["metadata"]["hash"] == sample_data["hash"]


# ============================================================================
# Validation Tests
# ============================================================================

class TestValidation:
    """Tests for input validation."""

    async def test_empty_checkpoint_name(self, client):
        """Test empty checkpoint name validation."""
        with pytest.raises(DLTClientValidationError) as exc:
            await client.write_checkpoint(
                checkpoint_name="",
                hash="hash",
                prev_hash="prev",
                metadata={},
                payload_blob=b"data"
            )
        assert "Checkpoint name cannot be empty" in str(exc.value)

    async def test_empty_hash(self, client):
        """Test empty hash validation."""
        with pytest.raises(DLTClientValidationError) as exc:
            await client.write_checkpoint(
                checkpoint_name="test",
                hash="",
                prev_hash="prev",
                metadata={},
                payload_blob=b"data"
            )
        assert "Hash cannot be empty" in str(exc.value)

    async def test_empty_payload(self, client):
        """Test empty payload validation."""
        with pytest.raises(DLTClientValidationError) as exc:
            await client.write_checkpoint(
                checkpoint_name="test",
                hash="hash",
                prev_hash="prev",
                metadata={},
                payload_blob=b""
            )
        assert "Payload blob cannot be empty" in str(exc.value)


# ============================================================================
# Chain State Persistence Tests
# ============================================================================

class TestPersistence:
    """Tests for chain state persistence."""

    async def test_dump_and_load_chain(self, tmp_path, mock_off_chain_client):
        """Test dumping and loading chain state."""
        chain_file = tmp_path / "test_chain.json"
        
        # First client without chain_state_path for initial creation
        config1 = {
            "simpledlt": {
                "log_format": "json",
                "temp_file_ttl": 3600.0,
                "cleanup_interval": 300.0
            }
        }
        
        # Create and populate client
        client1 = SimpleDLTClient(config1, mock_off_chain_client)
        await client1.initialize()
        
        await client1.write_checkpoint(
            checkpoint_name="test",
            hash="hash1",
            prev_hash="prev1",
            metadata={"test": "data"},
            payload_blob=b"payload1"
        )
        
        # Manually dump to file
        await client1.dump_chain(str(chain_file))
        await client1.close()
        
        # Now create config with chain_state_path for loading
        config2 = {
            "simpledlt": {
                "chain_state_path": str(chain_file),
                "log_format": "json",
                "temp_file_ttl": 3600.0,
                "cleanup_interval": 300.0
            }
        }
        
        # Load in new client
        client2 = SimpleDLTClient(config2, mock_off_chain_client)
        await client2.initialize()  # This will load the chain
        
        assert "test" in client2.chain
        assert client2.chain["test"][0]["hash"] == "hash1"
        await client2.close()

    async def test_checksum_calculation(self, client):
        """Test chain checksum calculation."""
        test_chain = {"test": [{"hash": "test_hash"}]}
        
        checksum1 = client._calculate_chain_checksum(test_chain)
        checksum2 = client._calculate_chain_checksum(test_chain)
        assert checksum1 == checksum2
        
        # Modify and verify different checksum
        test_chain["test"][0]["hash"] = "modified"
        checksum3 = client._calculate_chain_checksum(test_chain)
        assert checksum1 != checksum3


# ============================================================================
# Health Check Tests
# ============================================================================

class TestHealthCheck:
    """Tests for health check functionality."""

    async def test_health_check_success(self, client):
        """Test successful health check."""
        result = await client.health_check()
        
        assert result["status"] is True
        assert "SimpleDLT client is healthy" in result["message"]
        assert result["details"]["off_chain_status"] is True

    async def test_health_check_off_chain_failure(self, client):
        """Test health check with unhealthy off-chain client."""
        client.off_chain_client.health_check.return_value = {
            "status": False,
            "message": "Storage unavailable"
        }
        
        result = await client.health_check()
        assert result["status"] is False
        assert "Off-chain client health check failed" in result["message"]


# ============================================================================
# Concurrency Tests
# ============================================================================

class TestConcurrency:
    """Tests for concurrent operations."""

    async def test_concurrent_writes(self, client):
        """Test concurrent writes are properly serialized."""
        name = "concurrent_test"
        
        async def write(index):
            return await client.write_checkpoint(
                checkpoint_name=name,
                hash=f"hash_{index}",
                prev_hash=f"prev_{index}",
                metadata={"index": index},
                payload_blob=f"data_{index}".encode()
            )
        
        # Execute concurrent writes
        tasks = [write(i) for i in range(10)]
        results = await asyncio.gather(*tasks)
        
        # Verify all succeeded with unique versions
        versions = [r[2] for r in results]
        assert len(set(versions)) == 10
        assert sorted(versions) == list(range(1, 11))

    async def test_concurrent_reads(self, client, sample_data):
        """Test concurrent reads are safe."""
        # Write initial checkpoint
        await client.write_checkpoint(
            checkpoint_name=sample_data["name"],
            hash=sample_data["hash"],
            prev_hash=sample_data["prev_hash"],
            metadata=sample_data["metadata"],
            payload_blob=sample_data["payload"]
        )
        
        # Concurrent reads
        tasks = [client.read_checkpoint(sample_data["name"]) for _ in range(10)]
        results = await asyncio.gather(*tasks)
        
        # All should return same data
        for result in results:
            assert result["metadata"]["hash"] == sample_data["hash"]


# ============================================================================
# Error Handling Tests
# ============================================================================

class TestErrorHandling:
    """Tests for error handling and recovery."""

    async def test_off_chain_failure_propagation(self, client):
        """Test that off-chain failures are properly propagated."""
        client.off_chain_client.save_blob.side_effect = Exception("Storage error")
        
        with pytest.raises(DLTClientTransactionError) as exc:
            await client.write_checkpoint(
                checkpoint_name="test",
                hash="hash",
                prev_hash="prev",
                metadata={},
                payload_blob=b"data"
            )
        assert "Storage error" in str(exc.value)

    async def test_credential_rotation(self, client):
        """Test credential rotation."""
        client.off_chain_client._rotate_credentials = AsyncMock()
        await client._rotate_credentials(correlation_id="test-rotation")
        client.off_chain_client._rotate_credentials.assert_called_once()

    async def test_credential_rotation_not_supported(self, client):
        """Test when off-chain client doesn't support rotation."""
        # Remove the rotation method
        delattr(client.off_chain_client, '_rotate_credentials')
        
        # Should not raise exception
        await client._rotate_credentials()


# ============================================================================
# Plugin System Tests
# ============================================================================

class TestPluginSystem:
    """Tests for plugin system integration."""

    def test_plugin_manifest(self):
        """Test plugin manifest structure."""
        assert PLUGIN_MANIFEST["plugin_type"] == "dlt"
        assert PLUGIN_MANIFEST["name"] == "simpledlt"
        assert PLUGIN_MANIFEST["version"] == "1.0.0"
        assert callable(PLUGIN_MANIFEST["factory_function"])

    def test_factory_function(self, mock_off_chain_client, config):
        """Test factory function."""
        client = create_simple_dlt_client(config, mock_off_chain_client)
        assert isinstance(client, SimpleDLTClient)

    def test_factory_without_off_chain_client(self):
        """Test factory requires off-chain client."""
        with pytest.raises(DLTClientConfigurationError):
            create_simple_dlt_client({}, None)


# ============================================================================
# Performance Tests
# ============================================================================

class TestPerformance:
    """Basic performance tests."""

    async def test_write_performance(self, client):
        """Test write operation performance."""
        start = time.time()
        
        for i in range(100):
            await client.write_checkpoint(
                checkpoint_name=f"perf_test_{i}",
                hash=f"hash_{i}",
                prev_hash=f"prev_{i}",
                metadata={"index": i},
                payload_blob=b"x" * 1024
            )
        
        elapsed = time.time() - start
        avg_time = elapsed / 100
        
        # Should average less than 50ms per checkpoint
        assert avg_time < 0.05
        assert len(client.chain) == 100

    async def test_read_performance(self, client):
        """Test read operation performance."""
        name = "read_perf_test"
        
        # Write 100 versions
        for i in range(100):
            await client.write_checkpoint(
                checkpoint_name=name,
                hash=f"hash_{i}",
                prev_hash=f"prev_{i}",
                metadata={"v": i},
                payload_blob=b"data"
            )
        
        # Time reading specific version from deep history
        start = time.time()
        result = await client.read_checkpoint(name, version=50)
        elapsed = time.time() - start
        
        assert result["metadata"]["version"] == 50
        assert elapsed < 0.1  # Should be fast even with deep history


# ============================================================================
# Edge Cases
# ============================================================================

class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    async def test_unicode_metadata(self, client):
        """Test Unicode in metadata."""
        metadata = {
            "author": "张三",
            "description": "テスト",
            "emoji": "🚀✨"
        }
        
        await client.write_checkpoint(
            checkpoint_name="unicode_test",
            hash="hash",
            prev_hash="prev",
            metadata=metadata,
            payload_blob=b"data"
        )
        
        result = await client.read_checkpoint("unicode_test")
        assert result["metadata"]["metadata"] == metadata

    async def test_special_characters_in_name(self, client):
        """Test special characters in checkpoint name."""
        name = "test-checkpoint_2024.v1@#$"
        
        await client.write_checkpoint(
            checkpoint_name=name,
            hash="hash",
            prev_hash="prev",
            metadata={},
            payload_blob=b"data"
        )
        
        result = await client.read_checkpoint(name)
        assert result is not None

    async def test_none_metadata(self, client):
        """Test None metadata handling."""
        await client.write_checkpoint(
            checkpoint_name="none_meta",
            hash="hash",
            prev_hash="prev",
            metadata=None,
            payload_blob=b"data"
        )
        
        result = await client.read_checkpoint("none_meta")
        assert result["metadata"]["metadata"] == {}


# ============================================================================
# Cleanup Tests
# ============================================================================

class TestCleanup:
    """Tests for resource cleanup."""

    async def test_client_close(self, client):
        """Test proper cleanup on close."""
        # Add a cleanup task
        client._cleanup_task = asyncio.create_task(asyncio.sleep(10))
        
        await client.close()
        
        assert client._cleanup_task.cancelled()
        assert len(client.chain) == 0

    async def test_close_with_persistence(self, tmp_path, mock_off_chain_client):
        """Test close persists chain state."""
        chain_file = tmp_path / "close_test.json"
        
        # Start without chain_state_path
        config = {
            "simpledlt": {
                "log_format": "json",
                "temp_file_ttl": 3600.0,
                "cleanup_interval": 300.0
            }
        }
        
        client = SimpleDLTClient(config, mock_off_chain_client)
        await client.initialize()
        
        await client.write_checkpoint(
            checkpoint_name="test",
            hash="hash",
            prev_hash="prev",
            metadata={},
            payload_blob=b"data"
        )
        
        # Set the chain_state_path after initialization
        client._chain_state_path = str(chain_file)
        
        # Close should persist state
        await client.close()
        
        # Verify persistence
        assert chain_file.exists()
        with open(chain_file) as f:
            saved = json.load(f)
        assert "test" in saved


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x"])