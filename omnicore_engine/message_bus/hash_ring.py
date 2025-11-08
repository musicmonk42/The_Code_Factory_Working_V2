# message_bus/hash_ring.py

import bisect
import hashlib
import logging
from typing import List, Callable

logger = logging.getLogger(__name__)


class ConsistentHashRing:
    def __init__(self, nodes: List[str], replicas: int = 100):
        self.replicas = replicas
        self.ring = []
        self.nodes = []
        if not nodes:
            logger.warning("Initializing ConsistentHashRing with no nodes.")
        for node in nodes:
            # We add nodes one by one to use the internal add_node logic.
            self.add_node(node)
        logger.info("ConsistentHashRing initialized.", nodes=self.nodes, replicas=self.replicas)

    def add_node(self, node: str) -> None:
        """
        Adds a node to the hash ring, preventing duplicates.
        """
        if node in self.nodes:
            logger.warning("Attempted to add a duplicate node to the hash ring. Skipping.", node=node)
            return

        for i in range(self.replicas):
            key = self._hash(f"{node}:{i}")
            bisect.insort(self.ring, (key, node))
        self.nodes.append(node)
        self.nodes.sort()  # Keep the list of nodes sorted for consistency
        logger.info("Added node to hash ring.", node=node)

    def remove_node(self, node: str) -> None:
        """
        Removes a node from the hash ring.
        """
        if node not in self.nodes:
            logger.warning("Attempted to remove non-existent node from hash ring.", node=node)
            return
        
        # Rebuild the ring without the specified node's replicas.
        self.ring = [(key, n) for key, n in self.ring if n != node]
        self.nodes.remove(node)
        logger.info("Removed node from hash ring.", node=node)

    def get_node(self, key: str) -> str:
        """
        Given a key, finds the node responsible for it.
        """
        if not self.ring:
            raise ValueError("No nodes in hash ring. Cannot get node.")
        
        hash_key = self._hash(key)
        
        # bisect_left finds the insertion point, which is the position of the first element >= hash_key.
        # This is the "next" replica in the ring.
        idx = bisect.bisect_left(self.ring, (hash_key, ""))
        
        # If we loop past the end of the ring, we wrap around to the first element.
        if idx == len(self.ring):
            idx = 0
            
        return self.ring[idx][1]

    def _hash(self, key: str) -> int:
        """
        Generates a secure hash for a given key using SHA256.
        Returns a 64-bit integer.
        """
        # Using sha256 for better security and collision resistance compared to md5.
        hash_digest = hashlib.sha256(key.encode('utf-8')).hexdigest()
        # Take the first 16 characters (64 bits) to keep the integer size manageable
        return int(hash_digest[:16], 16)

    def add_node_dynamic(self, node: str, rebalance_callback: Callable[[str, List[str]], None]) -> None:
        """Adds a node and triggers rebalancing of affected keys."""
        if node in self.nodes:
            logger.warning(f"Node {node} already exists.")
            return
        self.add_node(node)  # Use existing add_node
        affected_keys = self._get_affected_keys(node)  # New helper to find remapped keys
        rebalance_callback(node, affected_keys)  # Callback to migrate messages

    def remove_node_dynamic(self, node: str, rebalance_callback: Callable[[str, List[str]], None]) -> None:
        """Removes a node and rebalances its keys to remaining nodes."""
        if node not in self.nodes:
            logger.warning(f"Node {node} not found.")
            return
        affected_keys = self._get_affected_keys(node, is_remove=True)
        self.remove_node(node)  # Use existing remove_node
        rebalance_callback(node, affected_keys)

    def _get_affected_keys(self, node: str, is_remove: bool = False) -> List[str]:
        """Placeholder: Simulate or track keys hashed to this node. In production, integrate with a key tracker."""
        # For simplicity, assume a tracked list of topics/keys; in real impl, query queues or DB
        return []  # Return list of affected topics/keys for rehashing