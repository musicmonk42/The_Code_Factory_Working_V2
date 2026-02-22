# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Consistent-hashing shard ring for the OmniCore message bus.

Provides :class:`ConsistentHashRing` — a thread-safe implementation that
maps arbitrary string keys to shard identifiers using a virtual-node ring.
This is the "sync / threading" counterpart to the async-first
:class:`~omnicore_engine.message_bus.hash_ring.ConsistentHashRing`.

Environment Variables:
    MESSAGE_BUS_SHARDS (int): Number of shards to create when the ring is
        initialised from the environment (default: ``3``).

Usage::

    from omnicore_engine.sharding import ConsistentHashRing

    ring = ConsistentHashRing()
    ring.add_shard("shard-0")
    ring.add_shard("shard-1")
    ring.add_shard("shard-2")

    shard = ring.get_shard("my.topic.name")  # e.g. "shard-1"
"""

from __future__ import annotations

import bisect
import hashlib
import logging
import os
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Number of virtual nodes placed on the ring per shard.
# Higher values → more uniform key distribution; lower values → faster ops.
_VIRTUAL_NODES_PER_SHARD: int = 150


class ConsistentHashRing:
    """
    Thread-safe consistent-hashing ring for shard routing.

    Each shard is represented by ``virtual_nodes`` virtual nodes distributed
    evenly around a 64-bit hash ring.  Routing a key requires O(log V·N) time
    where V is the number of virtual nodes and N is the number of shards.

    Thread-safety is guaranteed by a :class:`threading.RLock` so that the
    same instance can be read and modified from multiple threads concurrently.

    Args:
        virtual_nodes: Number of virtual nodes per shard.  Defaults to
            :data:`_VIRTUAL_NODES_PER_SHARD` (150).
    """

    def __init__(self, virtual_nodes: int = _VIRTUAL_NODES_PER_SHARD) -> None:
        self._virtual_nodes: int = virtual_nodes
        self._lock: threading.RLock = threading.RLock()
        # Sorted list of (hash_value, shard_id) tuples — the ring itself.
        self._ring: List[tuple] = []
        # Set of known shard IDs for O(1) membership checks.
        self._shards: Dict[str, bool] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_shard(self, shard_id: str) -> None:
        """
        Add a shard to the ring.

        If *shard_id* is already present, this is a no-op (with a warning).

        Args:
            shard_id: Unique string identifier for the new shard
                (e.g. ``"shard-0"`` or ``"redis://host:6379/0"``).
        """
        with self._lock:
            if shard_id in self._shards:
                logger.warning(
                    "ConsistentHashRing.add_shard: shard %r already present, skipping.",
                    shard_id,
                )
                return
            for vnode in range(self._virtual_nodes):
                h = self._hash(f"{shard_id}#{vnode}")
                bisect.insort(self._ring, (h, shard_id))
            self._shards[shard_id] = True
            logger.info(
                "ConsistentHashRing: added shard %r (%d virtual nodes, %d total shards).",
                shard_id,
                self._virtual_nodes,
                len(self._shards),
            )

    def remove_shard(self, shard_id: str) -> None:
        """
        Remove a shard from the ring.

        If *shard_id* is not present, this is a no-op (with a warning).

        Args:
            shard_id: Identifier of the shard to remove.
        """
        with self._lock:
            if shard_id not in self._shards:
                logger.warning(
                    "ConsistentHashRing.remove_shard: shard %r not found, skipping.",
                    shard_id,
                )
                return
            self._ring = [(h, s) for h, s in self._ring if s != shard_id]
            del self._shards[shard_id]
            logger.info(
                "ConsistentHashRing: removed shard %r (%d shards remaining).",
                shard_id,
                len(self._shards),
            )

    def get_shard(self, key: str) -> str:
        """
        Return the shard ID responsible for *key*.

        Uses clockwise lookup on the virtual-node ring.

        Args:
            key: Arbitrary string key (e.g. a message-bus topic name).

        Returns:
            The shard ID that owns *key*.

        Raises:
            ValueError: If the ring is empty (no shards have been added).
        """
        with self._lock:
            if not self._ring:
                raise ValueError(
                    "ConsistentHashRing is empty — add at least one shard before routing."
                )
            h = self._hash(key)
            idx = bisect.bisect_right(self._ring, (h, ""))
            if idx == len(self._ring):
                idx = 0
            return self._ring[idx][1]

    @property
    def shard_count(self) -> int:
        """Number of shards currently in the ring."""
        with self._lock:
            return len(self._shards)

    @property
    def shard_ids(self) -> List[str]:
        """Sorted list of shard IDs currently in the ring."""
        with self._lock:
            return sorted(self._shards.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash(key: str) -> int:
        """Return a 64-bit integer hash of *key* using SHA-256."""
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return int(digest[:16], 16)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def build_ring_from_env() -> ConsistentHashRing:
    """
    Build a :class:`ConsistentHashRing` pre-populated with shards based on
    the ``MESSAGE_BUS_SHARDS`` environment variable.

    The shards are named ``shard-0``, ``shard-1``, …, ``shard-{N-1}``.

    Returns:
        A ready-to-use :class:`ConsistentHashRing` with
        ``MESSAGE_BUS_SHARDS`` (default 3) shards.
    """
    num_shards: int = int(os.environ.get("MESSAGE_BUS_SHARDS", "3"))
    ring = ConsistentHashRing()
    for i in range(num_shards):
        ring.add_shard(f"shard-{i}")
    logger.info(
        "ConsistentHashRing initialised from env: MESSAGE_BUS_SHARDS=%d.", num_shards
    )
    return ring
