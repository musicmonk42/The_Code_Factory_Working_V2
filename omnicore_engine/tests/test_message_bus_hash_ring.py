# test_hash_ring.py

# Note: This test file uses context managers (with patch(...)) instead of
# @patch decorators to ensure compatibility with pytest-xdist parallel execution.
# Decorators cause issues when pytest-xdist forks worker processes because mocked
# modules cannot be properly serialized across process boundaries.

import hashlib
import pytest
from collections import Counter, defaultdict
from unittest.mock import Mock

from omnicore_engine.message_bus.hash_ring import ConsistentHashRing


class TestConsistentHashRing:
    """Test suite for ConsistentHashRing class."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Set up test fixtures before each test."""
        self.nodes = ["node1", "node2", "node3"]
        self.ring = ConsistentHashRing(nodes=self.nodes, replicas=100)

    def test_initialization_with_nodes(self):
        """Test initialization with a list of nodes."""
        ring = ConsistentHashRing(nodes=["node1", "node2"], replicas=50)

        assert ring.replicas == 50
        assert len(ring.nodes) == 2
        assert "node1" in ring.nodes
        assert "node2" in ring.nodes

        # Check ring has correct number of entries (nodes * replicas)
        assert len(ring.ring) == 2 * 50

    def test_initialization_empty_nodes(self):
        """Test initialization with empty node list."""
        # Import patch locally to ensure pytest-xdist compatibility
        # (module-level @patch decorators fail with process forking)
        from unittest.mock import patch
        
        with patch("omnicore_engine.message_bus.hash_ring.logger") as mock_logger:
            ring = ConsistentHashRing(nodes=[], replicas=100)

            assert len(ring.nodes) == 0
            assert len(ring.ring) == 0

            # Should log warning
            mock_logger.warning.assert_called_with(
                "Initializing ConsistentHashRing with no nodes."
            )

    def test_initialization_default_replicas(self):
        """Test default replicas value."""
        ring = ConsistentHashRing(nodes=["node1"])
        assert ring.replicas == 100

    def test_add_node(self):
        """Test adding a new node to the ring."""
        ring = ConsistentHashRing(nodes=["node1"], replicas=10)
        initial_ring_size = len(ring.ring)

        ring.add_node("node2")

        # Check node was added
        assert "node2" in ring.nodes
        assert len(ring.nodes) == 2

        # Check ring size increased correctly
        assert len(ring.ring) == initial_ring_size + 10

        # Verify nodes are sorted
        assert ring.nodes == sorted(ring.nodes)

    def test_add_duplicate_node(self):
        """Test adding a duplicate node."""
        from unittest.mock import patch
        
        ring = ConsistentHashRing(nodes=["node1"], replicas=10)
        initial_ring_size = len(ring.ring)
        initial_node_count = len(ring.nodes)

        with patch("omnicore_engine.message_bus.hash_ring.logger") as mock_logger:
            ring.add_node("node1")

            # Should not add duplicate
            assert len(ring.nodes) == initial_node_count
            assert len(ring.ring) == initial_ring_size

            # Should log warning
            mock_logger.warning.assert_called_with(
                "Attempted to add a duplicate node to the hash ring. Skipping.",
                node="node1",
            )

    def test_remove_node(self):
        """Test removing a node from the ring."""
        ring = ConsistentHashRing(nodes=["node1", "node2"], replicas=10)
        initial_ring_size = len(ring.ring)

        ring.remove_node("node1")

        # Check node was removed
        assert "node1" not in ring.nodes
        assert len(ring.nodes) == 1

        # Check ring size decreased correctly
        assert len(ring.ring) == initial_ring_size - 10

        # Verify no "node1" entries remain in ring
        for _, node in ring.ring:
            assert node != "node1"

    def test_remove_nonexistent_node(self):
        """Test removing a node that doesn't exist."""
        from unittest.mock import patch
        
        ring = ConsistentHashRing(nodes=["node1"], replicas=10)
        initial_ring_size = len(ring.ring)
        initial_node_count = len(ring.nodes)

        with patch("omnicore_engine.message_bus.hash_ring.logger") as mock_logger:
            ring.remove_node("nonexistent")

            # Should not change ring
            assert len(ring.nodes) == initial_node_count
            assert len(ring.ring) == initial_ring_size

            # Should log warning
            mock_logger.warning.assert_called_with(
                "Attempted to remove non-existent node from hash ring.", node="nonexistent"
            )

    def test_get_node_basic(self):
        """Test getting a node for a key."""
        ring = ConsistentHashRing(nodes=["node1", "node2", "node3"], replicas=100)

        # Test multiple keys
        keys = ["key1", "key2", "key3", "user_123", "session_456"]

        for key in keys:
            node = ring.get_node(key)
            assert node in ["node1", "node2", "node3"]

    def test_get_node_empty_ring(self):
        """Test getting a node when ring is empty."""
        ring = ConsistentHashRing(nodes=[], replicas=100)

        with pytest.raises(ValueError) as context:
            ring.get_node("any_key")

        assert "No nodes in hash ring" in str(context.value)

    def test_get_node_consistency(self):
        """Test that same key always maps to same node."""
        ring = ConsistentHashRing(nodes=["node1", "node2", "node3"], replicas=100)

        key = "consistent_key"

        # Get node multiple times
        nodes = [ring.get_node(key) for _ in range(10)]

        # All should be the same
        assert len(set(nodes)) == 1

    def test_get_node_distribution(self):
        """Test that keys are distributed across nodes."""
        ring = ConsistentHashRing(nodes=["node1", "node2", "node3"], replicas=100)

        # Generate many keys and check distribution
        distribution = defaultdict(int)

        for i in range(1000):
            key = f"key_{i}"
            node = ring.get_node(key)
            distribution[node] += 1

        # Each node should get some keys
        assert len(distribution) == 3

        # Distribution should be somewhat balanced (each node gets at least 20%)
        for node, count in distribution.items():
            assert count > 200  # At least 20% of 1000
            assert count < 500  # At most 50% of 1000

    def test_hash_function(self):
        """Test the hash function properties."""
        ring = ConsistentHashRing(nodes=["node1"], replicas=1)

        # Test determinism
        hash1 = ring._hash("test_key")
        hash2 = ring._hash("test_key")
        assert hash1 == hash2

        # Test different keys produce different hashes
        hash3 = ring._hash("different_key")
        assert hash1 != hash3

        # Test hash is an integer
        assert isinstance(hash1, int)

        # Test hash is positive
        assert hash1 > 0

    def test_ring_ordering(self):
        """Test that ring maintains sorted order."""
        ring = ConsistentHashRing(nodes=["node1", "node2"], replicas=10)

        # Check ring is sorted by hash values
        hash_values = [hash_val for hash_val, _ in ring.ring]
        assert hash_values == sorted(hash_values)

    def test_replica_distribution(self):
        """Test that replicas are well distributed."""
        ring = ConsistentHashRing(nodes=["node1"], replicas=100)

        # Get all hash values for the node's replicas
        hash_values = [hash_val for hash_val, node in ring.ring if node == "node1"]

        # Check we have correct number of replicas
        assert len(hash_values) == 100

        # Check replicas are spread out (no clustering)
        # Sort and check gaps between consecutive replicas
        hash_values.sort()
        gaps = []
        for i in range(1, len(hash_values)):
            gaps.append(hash_values[i] - hash_values[i - 1])

        # At least some variety in gap sizes (not all identical)
        assert len(set(gaps)) > 1

    def test_add_node_dynamic(self):
        """Test dynamic node addition with rebalancing callback."""
        ring = ConsistentHashRing(nodes=["node1", "node2"], replicas=10)

        # Mock rebalance callback
        rebalance_callback = Mock()

        ring.add_node_dynamic("node3", rebalance_callback)

        # Node should be added
        assert "node3" in ring.nodes

        # Callback should be called
        rebalance_callback.assert_called_once_with("node3", [])

    def test_add_node_dynamic_duplicate(self):
        """Test dynamic addition of duplicate node."""
        from unittest.mock import patch
        
        ring = ConsistentHashRing(nodes=["node1"], replicas=10)
        rebalance_callback = Mock()

        with patch("omnicore_engine.message_bus.hash_ring.logger") as mock_logger:
            ring.add_node_dynamic("node1", rebalance_callback)

            # Callback should not be called
            rebalance_callback.assert_not_called()

            # Should log warning
            mock_logger.warning.assert_called_with("Node node1 already exists.")

    def test_remove_node_dynamic(self):
        """Test dynamic node removal with rebalancing callback."""
        ring = ConsistentHashRing(nodes=["node1", "node2"], replicas=10)

        # Mock rebalance callback
        rebalance_callback = Mock()

        ring.remove_node_dynamic("node1", rebalance_callback)

        # Node should be removed
        assert "node1" not in ring.nodes

        # Callback should be called
        rebalance_callback.assert_called_once_with("node1", [])

    def test_remove_node_dynamic_nonexistent(self):
        """Test dynamic removal of non-existent node."""
        from unittest.mock import patch
        
        ring = ConsistentHashRing(nodes=["node1"], replicas=10)
        rebalance_callback = Mock()

        with patch("omnicore_engine.message_bus.hash_ring.logger") as mock_logger:
            ring.remove_node_dynamic("node2", rebalance_callback)

            # Callback should not be called
            rebalance_callback.assert_not_called()

            # Should log warning
            mock_logger.warning.assert_called_with("Node node2 not found.")

    def test_wrap_around(self):
        """Test wrap-around behavior when hash is larger than all ring values."""
        ring = ConsistentHashRing(nodes=["node1", "node2"], replicas=2)

        # Create a key that hashes to a very large value
        # We'll mock the hash function temporarily
        original_hash = ring._hash

        # Make hash return a value larger than any in the ring
        ring._hash = lambda key: 2**64 - 1 if key == "large_key" else original_hash(key)

        # Should wrap around to first node in ring
        node = ring.get_node("large_key")
        assert node in ["node1", "node2"]

        # Restore original hash
        ring._hash = original_hash

    def test_node_failure_scenario(self):
        """Test node failure and key redistribution."""
        ring = ConsistentHashRing(nodes=["node1", "node2", "node3"], replicas=100)

        # Map keys to nodes before failure
        keys = [f"key_{i}" for i in range(100)]
        before_failure = {key: ring.get_node(key) for key in keys}

        # Count keys per node
        before_counts = Counter(before_failure.values())

        # Simulate node2 failure
        ring.remove_node("node2")

        # Map keys after failure
        after_failure = {key: ring.get_node(key) for key in keys}

        # Count keys per node after
        after_counts = Counter(after_failure.values())

        # node2 should have no keys
        assert after_counts.get("node2", 0) == 0

        # Keys from node2 should be redistributed to node1 and node3
        node2_keys = [k for k, v in before_failure.items() if v == "node2"]
        for key in node2_keys:
            assert after_failure[key] in ["node1", "node3"]

        # Keys not on node2 should stay on same node
        for key in keys:
            if before_failure[key] != "node2":
                assert before_failure[key] == after_failure[key]

    def test_incremental_scaling(self):
        """Test scaling from 1 to many nodes."""
        ring = ConsistentHashRing(nodes=["node1"], replicas=50)

        # Start with single node
        key = "test_key"
        assert ring.get_node(key) == "node1"

        # Add nodes incrementally
        ring.add_node("node2")
        node_after_2 = ring.get_node(key)
        assert node_after_2 in ["node1", "node2"]

        ring.add_node("node3")
        node_after_3 = ring.get_node(key)
        assert node_after_3 in ["node1", "node2", "node3"]

        ring.add_node("node4")
        node_after_4 = ring.get_node(key)
        assert node_after_4 in ["node1", "node2", "node3", "node4"]

    def test_sha256_properties(self):
        """Test SHA256 hash properties used in the implementation."""
        ring = ConsistentHashRing(nodes=["node1"], replicas=1)

        # Test that SHA256 is being used (64-bit from first 16 hex chars)
        test_key = "test"
        hash_val = ring._hash(test_key)

        # Manually compute expected hash
        expected_hash = hashlib.sha256(test_key.encode("utf-8")).hexdigest()[:16]
        expected_val = int(expected_hash, 16)

        assert hash_val == expected_val

    def test_collision_handling(self):
        """Test that hash collisions are handled (though very unlikely with SHA256)."""
        ring = ConsistentHashRing(nodes=["node1", "node2"], replicas=1)

        # Even if two replicas somehow had same hash, bisect handles it
        # We can't easily force a collision with SHA256, but we can test
        # that duplicate hash values in ring don't break get_node

        # Manually add duplicate entries (simulating collision)
        test_hash = 12345
        ring.ring = [(test_hash, "node1"), (test_hash, "node2")]

        # Should still work without error
        ring._hash = lambda key: test_hash - 1  # Return hash just before duplicates
        node = ring.get_node("any_key")
        assert node in ["node1", "node2"]


class TestConsistentHashRingPerformance:
    """Performance tests for ConsistentHashRing."""

    def test_large_scale_nodes(self):
        """Test with large number of nodes."""
        nodes = [f"node_{i}" for i in range(100)]
        ring = ConsistentHashRing(nodes=nodes, replicas=100)

        # Should handle large number of nodes
        assert len(ring.nodes) == 100
        assert len(ring.ring) == 100 * 100

        # Should still distribute keys
        distribution = defaultdict(int)
        for i in range(1000):
            node = ring.get_node(f"key_{i}")
            distribution[node] += 1

        # Most nodes should get at least one key
        assert len(distribution) > 50

    def test_get_node_performance(self):
        """Test get_node performance with large ring."""
        import time

        ring = ConsistentHashRing(nodes=[f"node_{i}" for i in range(50)], replicas=200)

        # Time many lookups
        start = time.time()
        for i in range(10000):
            ring.get_node(f"key_{i}")
        elapsed = time.time() - start

        # Should be fast (less than 1 second for 10000 lookups)
        assert elapsed < 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

