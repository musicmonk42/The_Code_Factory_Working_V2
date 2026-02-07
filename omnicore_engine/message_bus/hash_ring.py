# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# message_bus/hash_ring.py

import asyncio
import bisect
import hashlib
from typing import Callable, List, Optional

import structlog

logger = structlog.get_logger(__name__)


class ConsistentHashRing:
    """
    A consistent hash ring implementation for distributed message routing.

    Thread-safety: Uses asyncio.Lock for async-safe operations (Issue #10 fix).
    For synchronous access, use the sync variants or acquire the lock manually.
    """

    def __init__(self, nodes: List[str], replicas: int = 100):
        self.replicas = replicas
        self.ring = []
        self.nodes = []
        self._lock: Optional[asyncio.Lock] = None  # Lazy initialization for async lock
        if not nodes:
            logger.warning("Initializing ConsistentHashRing with no nodes.")
        for node in nodes:
            # We add nodes one by one to use the internal add_node logic.
            self._add_node_sync(node)
        logger.info(
            "ConsistentHashRing initialized.", nodes=self.nodes, replicas=self.replicas
        )

    def _get_lock(self) -> Optional[asyncio.Lock]:
        """Get or create the asyncio lock (lazy initialization). Returns None if no event loop."""
        if self._lock is None:
            try:
                asyncio.get_running_loop()
                self._lock = asyncio.Lock()
            except RuntimeError:
                # No running event loop - likely in sync/test context
                return None
        return self._lock

    def _add_node_sync(self, node: str) -> None:
        """Synchronous node addition (for use during __init__ or with external locking)."""
        if node in self.nodes:
            logger.warning(
                "Attempted to add a duplicate node to the hash ring. Skipping.",
                node=node,
            )
            return

        for i in range(self.replicas):
            key = self._hash(f"{node}:{i}")
            bisect.insort(self.ring, (key, node))
        self.nodes.append(node)
        self.nodes.sort()  # Keep the list of nodes sorted for consistency
        logger.info("Added node to hash ring.", node=node)

    def add_node(self, node: str) -> None:
        """
        Adds a node to the hash ring, preventing duplicates.
        Note: For async contexts, prefer add_node_async().
        """
        self._add_node_sync(node)

    async def add_node_async(self, node: str) -> None:
        """Async-safe version of add_node."""
        lock = self._get_lock()
        if lock is not None:
            async with lock:
                self._add_node_sync(node)
        else:
            self._add_node_sync(node)

    def _remove_node_sync(self, node: str) -> None:
        """Synchronous node removal."""
        if node not in self.nodes:
            logger.warning(
                "Attempted to remove non-existent node from hash ring.", node=node
            )
            return

        # Rebuild the ring without the specified node's replicas.
        self.ring = [(key, n) for key, n in self.ring if n != node]
        self.nodes.remove(node)
        logger.info("Removed node from hash ring.", node=node)

    def remove_node(self, node: str) -> None:
        """
        Removes a node from the hash ring.
        Note: For async contexts, prefer remove_node_async().
        """
        self._remove_node_sync(node)

    async def remove_node_async(self, node: str) -> None:
        """Async-safe version of remove_node."""
        lock = self._get_lock()
        if lock is not None:
            async with lock:
                self._remove_node_sync(node)
        else:
            self._remove_node_sync(node)

    def _get_node_sync(self, key: str) -> str:
        """Synchronous node lookup."""
        if not self.ring:
            raise ValueError("No nodes in hash ring. Cannot get node.")

        hash_key = self._hash(key)

        # bisect_right finds the insertion point after any matching values.
        # This gives us the "next" replica clockwise on the ring.
        idx = bisect.bisect_right(self.ring, (hash_key, ""))

        # If we loop past the end of the ring, we wrap around to the first element.
        if idx == len(self.ring):
            idx = 0

        return self.ring[idx][1]

    def get_node(self, key: str) -> str:
        """
        Given a key, finds the node responsible for it.
        Note: For async contexts, prefer get_node_async() for consistency.
        """
        return self._get_node_sync(key)

    async def get_node_async(self, key: str) -> str:
        """Async-safe version of get_node."""
        lock = self._get_lock()
        if lock is not None:
            async with lock:
                return self._get_node_sync(key)
        else:
            return self._get_node_sync(key)

    def _hash(self, key: str) -> int:
        """
        Generates a secure hash for a given key using SHA256.
        Returns a 64-bit integer.
        """
        # Using sha256 for better security and collision resistance compared to md5.
        hash_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        # Take the first 16 characters (64 bits) to keep the integer size manageable
        return int(hash_digest[:16], 16)

    def add_node_dynamic(
        self, node: str, rebalance_callback: Callable[[str, List[str]], None]
    ) -> None:
        """Adds a node and triggers rebalancing of affected keys."""
        if node in self.nodes:
            logger.warning(f"Node {node} already exists.")
            return
        self.add_node(node)  # Use existing add_node
        affected_keys = self._get_affected_keys(
            node
        )  # New helper to find remapped keys
        rebalance_callback(node, affected_keys)  # Callback to migrate messages

    def remove_node_dynamic(
        self, node: str, rebalance_callback: Callable[[str, List[str]], None]
    ) -> None:
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
