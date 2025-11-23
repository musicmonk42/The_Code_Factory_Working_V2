# arbiter/learner/audit.py

import hashlib
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import structlog

# Assuming `audit_log` and `metrics` modules are available in the project root.
from arbiter.audit_log import log_event as audit_log
from prometheus_client import Gauge
from tenacity import retry, stop_after_attempt, wait_exponential

from .metrics import learn_error_counter

logger = structlog.get_logger(__name__)

# Metric for CB state
circuit_breaker_state = Gauge(
    "circuit_breaker_state", "Circuit breaker state (1=open, 0=closed)", ["name"]
)


class CircuitBreaker:
    """Manages circuit breaker state to prevent DB overload on failures."""

    def __init__(
        self,
        failure_threshold: int = int(os.getenv("CB_FAILURE_THRESHOLD", 5)),
        cooldown_seconds: int = int(os.getenv("CB_COOLDOWN_SECONDS", 60)),
        name: str = "default",
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.failures = 0
        self.last_failure: Optional[datetime] = None
        self.is_open = False
        circuit_breaker_state.labels(name=self.name).set(0)  # Initial closed
        logger.info(
            "CircuitBreaker initialized",
            name=self.name,
            threshold=self.failure_threshold,
            cooldown=self.cooldown_seconds,
        )

    async def record_failure(self):
        self.failures += 1
        self.last_failure = datetime.now(timezone.utc)
        if self.failures >= self.failure_threshold and not self.is_open:
            self.is_open = True
            circuit_breaker_state.labels(name=self.name).set(1)
            logger.warning("Circuit breaker opened", name=self.name, failures=self.failures)
        else:
            logger.debug("Circuit breaker failures", name=self.name, failures=self.failures)

    async def record_success(self):
        if self.failures > 0 or self.is_open:
            self.failures = 0
            self.is_open = False
            self.last_failure = None
            circuit_breaker_state.labels(name=self.name).set(0)
            logger.debug("Circuit breaker reset", name=self.name)

    async def can_proceed(self) -> bool:
        if not self.is_open:
            return True
        if self.last_failure:
            elapsed = (datetime.now(timezone.utc) - self.last_failure).total_seconds()
            if elapsed > self.cooldown_seconds:
                self.is_open = False
                self.failures = 0
                self.last_failure = None
                circuit_breaker_state.labels(name=self.name).set(0)
                logger.info(
                    "Circuit breaker closed after cooldown",
                    name=self.name,
                    elapsed=elapsed,
                )
                return True
        remaining = max(
            0,
            self.cooldown_seconds
            - (
                (datetime.now(timezone.utc) - self.last_failure).total_seconds()
                if self.last_failure
                else 0
            ),
        )
        logger.warning("Circuit breaker open", name=self.name, remaining_cooldown=remaining)
        return False


class MerkleTree:
    """Enhanced Merkle Tree for cryptographic integrity checking."""

    def __init__(self, leaves: List[bytes]):
        if not all(isinstance(leaf, bytes) for leaf in leaves):
            raise ValueError("All leaves must be bytes.")

        # Handle empty tree case - use hash of empty bytes
        if not leaves:
            self._leaves = []
            self._tree_levels = []
            self._root = self._hash(b"")
            return

        self._leaves = leaves[:]
        # Pad leaves to an even number for the tree structure
        if len(self._leaves) % 2 != 0:
            self._leaves.append(self._leaves[-1])

        # This builds a balanced Merkle tree.
        self._tree_levels = self._build_tree_levels(self._leaves)
        self._root = (
            self._tree_levels[-1][0]
            if self._tree_levels and self._tree_levels[-1]
            else self._hash(b"")
        )

    def _hash(self, data: bytes) -> bytes:
        return hashlib.sha256(data).digest()

    def _build_tree_levels(self, leaves: List[bytes]) -> List[List[bytes]]:
        """Builds all levels of the Merkle tree for efficient proof generation."""
        levels = [leaves]
        current_level = leaves
        while len(current_level) > 1:
            next_level = []
            for i in range(0, len(current_level), 2):
                left = current_level[i]
                right = current_level[i + 1]
                next_level.append(self._hash(left + right))
            levels.append(next_level)
            current_level = next_level
        return levels

    def get_root(self) -> bytes:
        return self._root

    def get_proof(self, index: int) -> List[Tuple[str, str]]:
        if not (0 <= index < len(self._leaves)):
            raise IndexError("Leaf index out of range")

        proof = []
        current_index = index
        for i in range(len(self._tree_levels) - 1):
            current_level = self._tree_levels[i]
            is_right_node = current_index % 2 != 0
            sibling_index = current_index - 1 if is_right_node else current_index + 1

            # The sibling should always exist due to padding
            sibling_hash = current_level[sibling_index].hex()
            proof.append((sibling_hash, "left" if is_right_node else "right"))

            current_index //= 2

        return proof

    def serialize(self) -> Dict[str, Any]:
        """Serializes the Merkle tree to a dictionary."""
        return {
            "root": self.get_root().hex(),
            "leaves": [leaf.hex() for leaf in self._leaves],
        }

    @staticmethod
    def deserialize(data: Dict[str, Any]) -> "MerkleTree":
        """Deserializes a Merkle tree from a dictionary."""
        leaves = [bytes.fromhex(leaf_hex) for leaf_hex in data["leaves"]]
        return MerkleTree(leaves)


async def _persist_knowledge_inner(
    db: Any,
    circuit_breaker: CircuitBreaker,
    domain: str,
    key: str,
    value_with_metadata: Dict[str, Any],
    user_id: Optional[str],
    leaf_hash: str,
    merkle_proof: List[Tuple[str, str]],
    merkle_root: str,
):
    """Inner function that performs the actual persistence logic."""
    try:
        await db.save_agent_knowledge(
            domain, key, value_with_metadata, value_with_metadata["timestamp"]
        )

        if await circuit_breaker.can_proceed():
            audit_event_data = {
                "action": "learn_fact",
                "domain": domain,
                "key": key,
                "value_with_metadata": value_with_metadata,
                "user_id": user_id or "system",
                "timestamp": value_with_metadata["timestamp"],
                "merkle_leaf": leaf_hash,
                "merkle_proof": merkle_proof,
                "merkle_root": merkle_root,
            }
            await audit_log(
                "knowledge_learning", audit_event_data
            )  # Fixed: audit_log is a function, not an object
            await circuit_breaker.record_success()
        else:
            logger.error(
                "Audit log for learning skipped due to open circuit breaker",
                name=circuit_breaker.name,
            )
            learn_error_counter.labels(domain=domain, error_type="audit_circuit_open_learn").inc()
    except Exception as e:
        learn_error_counter.labels(domain=domain, error_type="db_save_failure").inc()
        await circuit_breaker.record_failure()
        raise e


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
async def persist_knowledge(
    db: Any,
    circuit_breaker: CircuitBreaker,
    domain: str,
    key: str,
    value_with_metadata: Dict[str, Any],
    user_id: Optional[str],
    leaf_hash: str,
    merkle_proof: List[Tuple[str, str]],
    merkle_root: str,
):
    """Persist knowledge and create an audit event with retry logic."""
    return await _persist_knowledge_inner(
        db,
        circuit_breaker,
        domain,
        key,
        value_with_metadata,
        user_id,
        leaf_hash,
        merkle_proof,
        merkle_root,
    )


async def _persist_knowledge_batch_inner(
    db: Any,
    circuit_breaker: CircuitBreaker,
    entries: List[Tuple[str, str, Dict[str, Any], str, str, List[Tuple[str, str]], str]],
    user_id: Optional[str],
):
    """Inner function that performs the actual batch persistence logic."""
    try:
        db_entries = [
            (domain, key, value_with_metadata, timestamp)
            for domain, key, value_with_metadata, timestamp, _, _, _ in entries
        ]
        await db.save_agent_knowledge_batch(db_entries)

        if await circuit_breaker.can_proceed():
            audit_events = [
                {
                    "action": "learn_fact",
                    "domain": domain,
                    "key": key,
                    "value_with_metadata": value_with_metadata,
                    "user_id": user_id or "system",
                    "timestamp": timestamp,
                    "merkle_leaf": leaf_hash,
                    "merkle_proof": merkle_proof,
                    "merkle_root": merkle_root,
                }
                for domain, key, value_with_metadata, timestamp, leaf_hash, merkle_proof, merkle_root in entries
            ]
            await audit_log(
                "knowledge_learning_batch",
                {"entries": audit_events, "user_id": user_id},
            )  # Fixed: audit_log is a function, not an object
            await circuit_breaker.record_success()
        else:
            logger.error(
                "Batch audit skipped due to open circuit breaker",
                name=circuit_breaker.name,
            )
            learn_error_counter.labels(
                domain="batch", error_type="audit_circuit_open_batch_learn"
            ).inc()
    except Exception as e:
        learn_error_counter.labels(domain="batch", error_type="db_save_failure_batch").inc()
        await circuit_breaker.record_failure()
        raise e


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
async def persist_knowledge_batch(
    db: Any,
    circuit_breaker: CircuitBreaker,
    entries: List[Tuple[str, str, Dict[str, Any], str, str, List[Tuple[str, str]], str]],
    user_id: Optional[str],
):
    """Persist knowledge and create audit events in batch with retry logic."""
    return await _persist_knowledge_batch_inner(db, circuit_breaker, entries, user_id)
