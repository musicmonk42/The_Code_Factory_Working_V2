import asyncio
import gzip  # For compressed JSON
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Type, Union

# Import tenacity for retries with exponential backoff
from tenacity import retry, stop_after_attempt, wait_exponential

# Merklelib import
try:
    from merklelib import MerkleTree as MerkleLibTree

    # In some versions of merklelib, the function is verify_leaf_inclusion instead of verify_inclusion
    try:
        from merklelib import verify_inclusion
    except ImportError:
        from merklelib import verify_leaf_inclusion as verify_inclusion

    MERKLELIB_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).info(
        "merklelib library not found. MerkleTree will operate in mock mode."
    )
    MERKLELIB_AVAILABLE = False

    # Define dummy classes/functions to prevent NameError if merklelib is not installed
    class MerkleLibTree:
        def __init__(self, leaves: Optional[List[bytes]] = None):
            self._hashed_leaves_mock = leaves if leaves is not None else []
            self._root = self._compute_mock_root()
            logging.getLogger(__name__).warning(
                "Mock MerkleLibTree initialized. Merkle operations will be conceptual."
            )

        def _compute_mock_root(self) -> bytes:
            if not self._hashed_leaves_mock:
                return hashlib.sha256(b"").digest()
            return hashlib.sha256(b"".join(self._hashed_leaves_mock)).digest()

        def add_leaf(self, data: bytes):
            self._hashed_leaves_mock.append(data)
            self._root = self._compute_mock_root()

        def get_root(self) -> bytes:
            return self._root

        def get_proof(self, index: int) -> List[Tuple[bytes, str]]:
            if not (0 <= index < len(self._hashed_leaves_mock)):
                raise IndexError(
                    f"Mock MerkleLibTree: Leaf index {index} out of bounds for {len(self._hashed_leaves_mock)} leaves."
                )
            # Return a mock proof that can still be used conceptually
            return (
                [(hashlib.sha256(b"mock_sibling").digest(), "right")]
                if self._hashed_leaves_mock
                else []
            )

    def verify_inclusion(
        root: bytes, leaf: bytes, proof: List[Tuple[bytes, str]]
    ) -> bool:
        logging.getLogger(__name__).warning(
            "Mock verify_inclusion always returns True."
        )
        return True


# Define logger here before it's used
logger = logging.getLogger(__name__)
# Add null handler to prevent "No handler found" warnings
logger.addHandler(logging.NullHandler())


# Prometheus Metrics
try:
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client not found. Metrics will be disabled.")

    # Define no-op classes to prevent NameError
    class Counter:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

    class Gauge:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def set(self, *args, **kwargs):
            pass

    class Histogram:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def observe(self, *args, **kwargs):
            pass

    class CollectorRegistry:
        def __init__(self):
            pass


# OpenTelemetry Tracing - Using centralized configuration
from self_fixing_engineer.arbiter.otel_config import get_tracer
from opentelemetry.trace import Status, StatusCode

tracer = get_tracer(__name__)

METRICS_REGISTRY = CollectorRegistry() if PROMETHEUS_AVAILABLE else None
# Single knob for hashing offload thresholds
HASH_OFFLOAD_THRESHOLD = int(os.getenv("MERKLE_HASH_OFFLOAD", "256"))


# Ensure metrics are registered only once
def _get_or_create_metric(
    metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram]],
    name: str,
    documentation: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Optional[Tuple[float, ...]] = None,
):
    """
    Idempotently get or create a Prometheus metric.
    """
    if not PROMETHEUS_AVAILABLE:
        return metric_class(name, documentation, labelnames=labelnames)

    try:
        if buckets:
            return metric_class(
                name,
                documentation,
                labelnames=labelnames,
                buckets=buckets,
                registry=METRICS_REGISTRY,
            )
        return metric_class(
            name, documentation, labelnames=labelnames, registry=METRICS_REGISTRY
        )
    except ValueError as e:
        if "Duplicated timeseries" in str(e):
            existing_metric = getattr(METRICS_REGISTRY, "_names_to_collectors", {}).get(
                name
            )
            if existing_metric and isinstance(existing_metric, metric_class):
                return existing_metric
        raise


# Metrics for MerkleTree Operations
MERKLE_OPS_TOTAL = _get_or_create_metric(
    Counter, "merkle_ops_total", "Total Merkle tree operations", ["operation", "status"]
)
MERKLE_OPS_LATENCY_SECONDS = _get_or_create_metric(
    Histogram,
    "merkle_ops_latency_seconds",
    "Merkle tree operation latency in seconds",
    ["operation"],
    buckets=(0.001, 0.01, 0.1, 1, 2, 5, 10),
)
MERKLE_TREE_SIZE = _get_or_create_metric(
    Gauge, "merkle_tree_size", "Current number of leaves in Merkle tree"
)
MERKLE_TREE_DEPTH = _get_or_create_metric(
    Gauge, "merkle_tree_depth", "Current depth of the Merkle tree"
)


# Custom Exceptions
class MerkleTreeError(Exception):
    """Base exception for MerkleTree operations."""

    pass


class MerkleTreeEmptyError(MerkleTreeError):
    """Raised when an operation is attempted on an empty tree."""

    pass


class MerkleProofError(MerkleTreeError):
    """Raised when a Merkle proof is invalid or malformed."""

    pass


# Helper functions for synchronous file I/O to be used with asyncio.to_thread
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _write_compressed_json(path: str, data: Any) -> None:
    """Synchronously writes JSON data to a gzipped file atomically."""
    dir_ = os.path.dirname(path) or "."
    fd, tmp_name = tempfile.mkstemp(dir=dir_)
    os.close(fd)
    try:
        with gzip.open(tmp_name, "wt", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_name, path)
        logger.debug(f"Successfully wrote compressed JSON to {path}")
    finally:
        try:
            os.remove(tmp_name)
        except FileNotFoundError:
            pass


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
def _read_compressed_json(path: str) -> Any:
    """Synchronously reads JSON data from a gzipped file."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    logger.debug(f"Successfully read compressed JSON from {path}")
    return data


class MerkleTree:
    """
    A Merkle Tree implementation for data integrity auditing,
    leveraging the `merklelib` library.

    Provides methods for adding leaves, computing the root, generating proofs,
    and verifying proofs, with integrated observability and persistence.
    """

    def __init__(self, leaves: Optional[List[bytes]] = None, store_raw: bool = False):
        """
        Initializes the Merkle Tree.
        Args:
            leaves (Optional[List[bytes]]): Initial list of leaf data (bytes).
            store_raw (bool): If True, stores raw data in the internal leaves list. If False (default),
                              stores the hashes of the data.
        """
        self._leaves: List[bytes] = []
        self._tree: Optional[MerkleLibTree] = None
        self._lock = asyncio.Lock()
        self._rwlock = threading.RLock()  # guards sync reads during async writes
        self._store_raw = store_raw

        if leaves:
            if store_raw:
                self._leaves.extend(leaves)
            else:
                self._leaves.extend([self._hash_leaf(b) for b in leaves])

        if MERKLELIB_AVAILABLE:
            self._tree = MerkleLibTree(self._hashed_leaves())
            logger.info("MerkleTree initialized with merklelib.")
        else:
            logger.critical(
                "merklelib not available. MerkleTree functionality is disabled."
            )
            self._tree = MerkleLibTree([])

        self._update_metrics()

    def _hash_leaf(self, leaf_bytes: bytes) -> bytes:
        """Centralized helper for hashing leaves."""
        return hashlib.sha256(leaf_bytes).digest()

    def _hashed_leaves(self) -> List[bytes]:
        """Returns the list of hashed leaves, either from storage or by hashing raw leaves."""
        if self._store_raw:
            return [self._hash_leaf(b) for b in self._leaves]
        return self._leaves

    async def _update_tree(self) -> None:
        """Rebuilds the underlying merklelib tree from current leaves."""
        if not MERKLELIB_AVAILABLE:
            logger.warning("merklelib not available. Cannot rebuild tree.")
            return
        with self._rwlock:
            leaves = self._leaves
            if self._store_raw and len(leaves) > HASH_OFFLOAD_THRESHOLD:
                hashed = await asyncio.to_thread(
                    lambda: [self._hash_leaf(b) for b in leaves]
                )
            else:
                hashed = (
                    [self._hash_leaf(b) for b in leaves] if self._store_raw else leaves
                )
            self._tree = MerkleLibTree(hashed)
            self._update_metrics()

    def _update_metrics(self) -> None:
        try:
            MERKLE_TREE_SIZE.set(self.size)
            MERKLE_TREE_DEPTH.set(self.approx_depth)
        except Exception:
            pass

    @property
    def size(self) -> int:
        return len(self._leaves)

    @property
    def approx_depth(self) -> int:
        return (self.size - 1).bit_length() if self.size > 0 else 0

    def _root_bytes(self) -> bytes:
        """Internal helper to get root bytes, handling different merklelib API versions."""
        if not self._tree:
            raise MerkleTreeEmptyError("Merkle tree is empty or not initialized.")
        if hasattr(self._tree, "get_root"):
            rb = self._tree.get_root()
            return rb if isinstance(rb, (bytes, bytearray)) else bytes.fromhex(rb)
        if hasattr(self._tree, "merkle_root"):
            rb = self._tree.merkle_root
            return rb if isinstance(rb, (bytes, bytearray)) else bytes.fromhex(rb)
        raise MerkleTreeError("Unsupported merklelib version: cannot read root.")

    def _proof_for_index(self, idx: int) -> List[Tuple[bytes, str]]:
        """Internal helper to get proof for an index, handling different merklelib API versions."""
        if hasattr(self._tree, "get_proof"):
            try:
                return self._tree.get_proof(idx)
            except TypeError:
                target = (
                    self._leaves[idx]
                    if not self._store_raw
                    else self._hash_leaf(self._leaves[idx])
                )
                return self._tree.get_proof(target)
        raise MerkleTreeError("Unsupported merklelib version: cannot get proof.")

    async def add_leaf(self, data: Union[str, bytes]) -> None:
        """
        Adds a new data element to the Merkle tree.
        The data is hashed before being added to the underlying cryptographic tree,
        while the original data may be stored based on `store_raw` setting.
        Args:
            data (Union[str, bytes]): The data to add. If string, it's UTF-8 encoded.
        """
        async with self._lock:
            with self._rwlock:
                with tracer.start_as_current_span("merkle_add_leaf") as span:
                    start_time = time.monotonic()
                    MERKLE_OPS_TOTAL.labels(
                        operation="add_leaf", status="attempt"
                    ).inc()
                    try:
                        leaf_bytes = (
                            data.encode("utf-8") if isinstance(data, str) else data
                        )
                        if not MERKLELIB_AVAILABLE or not self._tree:
                            raise MerkleTreeError(
                                "MerkleTree not initialized or merklelib not available."
                            )

                        new_item = (
                            leaf_bytes
                            if self._store_raw
                            else self._hash_leaf(leaf_bytes)
                        )
                        self._leaves.append(new_item)

                        if MERKLELIB_AVAILABLE and hasattr(self._tree, "add_leaf"):
                            self._tree.add_leaf(self._hash_leaf(leaf_bytes))
                            if hasattr(self._tree, "make_tree"):
                                self._tree.make_tree()
                            self._update_metrics()
                        else:
                            await self._update_tree()

                        MERKLE_OPS_TOTAL.labels(
                            operation="add_leaf", status="success"
                        ).inc()
                        span.set_status(Status(StatusCode.OK))
                        logger.debug(
                            f"Added leaf to Merkle tree. Current leaves: {len(self._leaves)}"
                        )
                    except Exception as e:
                        MERKLE_OPS_TOTAL.labels(
                            operation="add_leaf", status="failure"
                        ).inc()
                        span.record_exception(e)
                        span.set_status(
                            Status(StatusCode.ERROR, f"Failed to add leaf: {e}")
                        )
                        logger.error(
                            f"Failed to add leaf to Merkle tree: {e}", exc_info=True
                        )
                        raise
                    finally:
                        MERKLE_OPS_LATENCY_SECONDS.labels(operation="add_leaf").observe(
                            time.monotonic() - start_time
                        )

    async def add_leaves(self, data_list: List[Union[str, bytes]]) -> None:
        """
        Adds multiple data elements as leaves to the Merkle tree in a batch.
        Args:
            data_list (List[Union[str, bytes]]): A list of data elements to add.
        """
        async with self._lock:
            with self._rwlock:
                with tracer.start_as_current_span("merkle_add_leaves") as span:
                    span.set_attribute("merkle.num_leaves_added", len(data_list))
                    start_time = time.monotonic()
                    MERKLE_OPS_TOTAL.labels(
                        operation="add_leaves", status="attempt"
                    ).inc()
                    try:
                        if not MERKLELIB_AVAILABLE or not self._tree:
                            raise MerkleTreeError(
                                "MerkleTree not initialized or merklelib not available."
                            )

                        new_items_raw = [
                            d.encode("utf-8") if isinstance(d, str) else d
                            for d in data_list
                        ]

                        # Never hash into _leaves when store_raw=True. Hashing is done in _update_tree().
                        if self._store_raw:
                            new_items = new_items_raw
                        else:
                            if len(new_items_raw) > HASH_OFFLOAD_THRESHOLD:
                                new_items = await asyncio.to_thread(
                                    lambda: [self._hash_leaf(b) for b in new_items_raw]
                                )
                            else:
                                new_items = [self._hash_leaf(b) for b in new_items_raw]

                        self._leaves.extend(new_items)

                        await self._update_tree()

                        MERKLE_OPS_TOTAL.labels(
                            operation="add_leaves", status="success"
                        ).inc()
                        span.set_status(Status(StatusCode.OK))
                        logger.debug(
                            f"Added {len(data_list)} leaves to Merkle tree. Current leaves: {len(self._leaves)}"
                        )
                    except Exception as e:
                        MERKLE_OPS_TOTAL.labels(
                            operation="add_leaves", status="failure"
                        ).inc()
                        span.record_exception(e)
                        span.set_status(
                            Status(StatusCode.ERROR, f"Failed to add leaves: {e}")
                        )
                        logger.error(
                            f"Failed to add leaves to Merkle tree: {e}", exc_info=True
                        )
                        raise
                    finally:
                        MERKLE_OPS_LATENCY_SECONDS.labels(
                            operation="add_leaves"
                        ).observe(time.monotonic() - start_time)

    def get_root(self) -> str:
        with self._rwlock:
            with tracer.start_as_current_span("merkle_get_root") as span:
                start_time = time.monotonic()
                MERKLE_OPS_TOTAL.labels(operation="get_root", status="attempt").inc()
                try:
                    if not self._leaves or not self._tree:
                        raise MerkleTreeEmptyError(
                            "Merkle tree is empty or not initialized."
                        )

                    root_bytes = self._root_bytes()
                    root = root_bytes.hex()

                    MERKLE_OPS_TOTAL.labels(
                        operation="get_root", status="success"
                    ).inc()
                    span.set_status(Status(StatusCode.OK))
                    logger.debug(f"Retrieved Merkle root: {root[:8]}...")
                    return root
                except MerkleTreeEmptyError as e:
                    MERKLE_OPS_TOTAL.labels(
                        operation="get_root", status="failure"
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Failed to get root: {e}")
                    )
                    logger.warning(f"Failed to get Merkle root: {e}")
                    raise
                except Exception as e:
                    MERKLE_OPS_TOTAL.labels(
                        operation="get_root", status="failure"
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Failed to get root: {e}")
                    )
                    logger.error(f"Failed to get Merkle root: {e}", exc_info=True)
                    raise
                finally:
                    MERKLE_OPS_LATENCY_SECONDS.labels(operation="get_root").observe(
                        time.monotonic() - start_time
                    )

    def get_proof(self, index: int) -> List[Dict[str, str]]:
        with self._rwlock:
            with tracer.start_as_current_span("merkle_get_proof") as span:
                span.set_attribute("merkle.proof_index", index)
                start_time = time.monotonic()
                MERKLE_OPS_TOTAL.labels(operation="get_proof", status="attempt").inc()
                try:
                    if not self._leaves:
                        raise MerkleTreeEmptyError(
                            "Attempted to get proof from an empty Merkle tree."
                        )
                    if not (0 <= index < len(self._leaves)):
                        raise IndexError(
                            f"Leaf index {index} out of bounds for {len(self._leaves)} leaves."
                        )

                    if not MERKLELIB_AVAILABLE or not self._tree:
                        raise MerkleTreeError(
                            "MerkleTree not properly initialized (merklelib not available). Cannot get proof."
                        )

                    proof_tuples = self._proof_for_index(index)

                    formatted_proof = [
                        {"node": node.hex(), "position": pos}
                        for node, pos in proof_tuples
                    ]

                    MERKLE_OPS_TOTAL.labels(
                        operation="get_proof", status="success"
                    ).inc()
                    span.set_status(Status(StatusCode.OK))
                    return formatted_proof
                except (IndexError, MerkleTreeEmptyError) as e:
                    MERKLE_OPS_TOTAL.labels(
                        operation="get_proof", status="failure"
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Failed to get proof: {e}")
                    )
                    logger.warning(f"Failed to get proof for index {index}: {e}")
                    raise
                except Exception as e:
                    MERKLE_OPS_TOTAL.labels(
                        operation="get_proof", status="failure"
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Failed to get proof: {e}")
                    )
                    logger.error(
                        f"Failed to get proof for index {index}: {e}", exc_info=True
                    )
                    raise MerkleProofError(f"Error generating proof: {e}") from e
                finally:
                    MERKLE_OPS_LATENCY_SECONDS.labels(operation="get_proof").observe(
                        time.monotonic() - start_time
                    )

    @staticmethod
    def verify_proof(
        root: str, leaf_data: Union[str, bytes], proof: List[Dict[str, str]]
    ) -> bool:
        """
        Verifies a Merkle proof for a given leaf against a Merkle root.
        Args:
            root (str): The hexadecimal string of the Merkle root.
            leaf_data (Union[str, bytes]): The original data of the leaf.
            proof (List[Dict[str, str]]): The Merkle proof. Each element is a dict with 'node' (hex string) and 'position'.
        Returns:
            bool: True if the proof is valid, False otherwise.
        Raises:
            MerkleProofError: If the proof format is invalid.
        """
        with tracer.start_as_current_span("merkle_verify_proof") as span:
            start_time = time.monotonic()
            MERKLE_OPS_TOTAL.labels(operation="verify_proof", status="attempt").inc()
            try:
                if not MERKLELIB_AVAILABLE:
                    logger.critical("merklelib not available. Cannot verify proof.")
                    return False

                root_bytes = bytes.fromhex(root)
                leaf_raw = (
                    leaf_data.encode("utf-8")
                    if isinstance(leaf_data, str)
                    else leaf_data
                )
                leaf_once = hashlib.sha256(leaf_raw).digest()
                leaf_twice = hashlib.sha256(leaf_once).digest()

                proof_for_lib: List[Tuple[bytes, str]] = []
                for node_data in proof:
                    if "node" in node_data and "position" in node_data:
                        try:
                            pos = node_data["position"].lower()
                            if pos not in ("left", "right"):
                                raise MerkleProofError(
                                    f"Invalid proof node position: {pos!r}"
                                )
                            proof_for_lib.append(
                                (bytes.fromhex(node_data["node"]), pos)
                            )
                        except ValueError:
                            raise MerkleProofError(
                                f"Invalid hex string in proof node: {node_data['node']}"
                            )
                    else:
                        raise MerkleProofError(
                            f"Malformed proof node: {node_data}. Expected 'node' and 'position'."
                        )

                is_valid = verify_inclusion(root_bytes, leaf_once, proof_for_lib)
                if not is_valid:
                    is_valid = verify_inclusion(root_bytes, leaf_twice, proof_for_lib)

                MERKLE_OPS_TOTAL.labels(
                    operation="verify_proof",
                    status="success" if is_valid else "failure",
                ).inc()
                span.set_status(Status(StatusCode.OK if is_valid else StatusCode.ERROR))
                if not is_valid:
                    logger.warning(
                        f"Merkle proof verification failed for leaf '{leaf_data}'. Root: {root}, Proof: {proof}"
                    )
                logger.debug(f"Merkle proof verification result: {is_valid}")
                return is_valid
            except MerkleProofError as e:
                MERKLE_OPS_TOTAL.labels(
                    operation="verify_proof", status="failure"
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Proof format error: {e}"))
                logger.error(f"Merkle proof format error: {e}", exc_info=True)
                return False
            except Exception as e:
                MERKLE_OPS_TOTAL.labels(
                    operation="verify_proof", status="failure"
                ).inc()
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, f"Failed to verify proof: {e}")
                )
                logger.error(f"Failed to verify Merkle proof: {e}", exc_info=True)
                return False
            finally:
                MERKLE_OPS_LATENCY_SECONDS.labels(operation="verify_proof").observe(
                    time.monotonic() - start_time
                )

    async def save(self, filepath: Optional[str] = None) -> None:
        """
        Persists the current state of the Merkle tree to a gzipped JSON file.
        This allows the tree to be reconstructed later.
        Args:
            filepath (Optional[str]): The path to the file where the tree state will be saved.
                                      Defaults to MERKLE_STORAGE_PATH/merkle_tree_state.json.gz.
        """
        if filepath is None:
            storage_path = os.getenv("MERKLE_STORAGE_PATH", "/var/merkle")
            os.makedirs(storage_path, exist_ok=True)
            filepath = os.path.join(storage_path, "merkle_tree_state.json.gz")

        async with self._lock:
            with self._rwlock:
                with tracer.start_as_current_span("merkle_save_tree") as span:
                    span.set_attribute("merkle.save_path", filepath)
                    start_time = time.monotonic()
                    MERKLE_OPS_TOTAL.labels(
                        operation="save_tree", status="attempt"
                    ).inc()
                    try:
                        payload = {
                            "version": 1,
                            "store_raw": self._store_raw,
                            "leaves": [leaf.hex() for leaf in self._leaves],
                        }
                        await asyncio.to_thread(
                            _write_compressed_json, filepath, payload
                        )
                        logger.info(f"Merkle tree state saved to {filepath}.")
                        MERKLE_OPS_TOTAL.labels(
                            operation="save_tree", status="success"
                        ).inc()
                        span.set_status(Status(StatusCode.OK))
                    except Exception as e:
                        MERKLE_OPS_TOTAL.labels(
                            operation="save_tree", status="failure"
                        ).inc()
                        span.record_exception(e)
                        span.set_status(
                            Status(StatusCode.ERROR, f"Failed to save tree: {e}")
                        )
                        logger.error(
                            f"Failed to save Merkle tree state to {filepath}: {e}",
                            exc_info=True,
                        )
                        raise
                    finally:
                        MERKLE_OPS_LATENCY_SECONDS.labels(
                            operation="save_tree"
                        ).observe(time.monotonic() - start_time)

    @classmethod
    async def load(cls, filepath: Optional[str] = None) -> "MerkleTree":
        """
        Loads the state of a Merkle tree from a gzipped JSON file and reconstructs it.
        Args:
            filepath (Optional[str]): The path to the file from which to load the tree state.
        Returns:
            MerkleTree: A new MerkleTree instance with the loaded state.
        """
        if filepath is None:
            storage_path = os.getenv("MERKLE_STORAGE_PATH", "/var/merkle")
            filepath = os.path.join(storage_path, "merkle_tree_state.json.gz")

        with tracer.start_as_current_span("merkle_load_tree") as span:
            span.set_attribute("merkle.load_path", filepath)
            start_time = time.monotonic()
            MERKLE_OPS_TOTAL.labels(operation="load_tree", status="attempt").inc()
            try:
                payload = await asyncio.to_thread(_read_compressed_json, filepath)

                if isinstance(payload, dict) and "leaves" in payload:
                    if not isinstance(payload.get("leaves"), list):
                        raise ValueError(
                            "Invalid persisted state: 'leaves' must be a list"
                        )
                    store_raw = bool(payload.get("store_raw", False))
                    loaded_leaves = [bytes.fromhex(x) for x in payload["leaves"]]
                elif isinstance(payload, list):
                    store_raw = False
                    loaded_leaves = [bytes.fromhex(x) for x in payload]
                else:
                    raise ValueError("Invalid persisted state format.")

                obj = cls(store_raw=store_raw)
                obj._leaves = loaded_leaves
                await obj._update_tree()

                logger.info(f"Merkle tree state loaded from {filepath}.")
                MERKLE_OPS_TOTAL.labels(operation="load_tree", status="success").inc()
                span.set_status(Status(StatusCode.OK))
                return obj
            except FileNotFoundError:
                logger.warning(
                    f"Merkle tree state file not found at {filepath}. Initializing empty tree."
                )
                MERKLE_OPS_TOTAL.labels(operation="load_tree", status="failure").inc()
                span.set_status(Status(StatusCode.ERROR, "File not found"))
                return cls()
            except (json.JSONDecodeError, gzip.BadGzipFile, ValueError) as e:
                logger.error(
                    f"Merkle tree state file at {filepath} is corrupted: {e}. Initializing empty tree.",
                    exc_info=True,
                )
                MERKLE_OPS_TOTAL.labels(operation="load_tree", status="failure").inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, "File corrupted"))
                return cls()
            except Exception as e:
                logger.error(
                    f"Failed to load Merkle tree state from {filepath}: {e}",
                    exc_info=True,
                )
                MERKLE_OPS_TOTAL.labels(operation="load_tree", status="failure").inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Unexpected load error: {e}"))
                raise
            finally:
                MERKLE_OPS_LATENCY_SECONDS.labels(operation="load_tree").observe(
                    time.monotonic() - start_time
                )


# Example Usage (for testing purposes)
async def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.setLevel(logging.DEBUG)

    logger.info("\n--- MerkleTree Example Usage ---")

    test_storage_dir = "./merkle_test_data"
    os.makedirs(test_storage_dir, exist_ok=True)
    os.environ["MERKLE_STORAGE_PATH"] = test_storage_dir
    test_filepath = os.path.join(test_storage_dir, "merkle_tree_state.json.gz")

    tree = MerkleTree()
    leaves_data = [
        "transaction_A_details_123",
        "log_entry_B_details_456",
        "config_change_C_details_789",
        "user_action_D_details_010",
    ]

    for i, data in enumerate(leaves_data):
        logger.info(f"Adding leaf {i}: '{data}'")
        await tree.add_leaf(data)

    batch_leaves = ["batch_item_1", "batch_item_2"]
    logger.info(f"Adding batch of {len(batch_leaves)} leaves.")
    await tree.add_leaves(batch_leaves)

    root = tree.get_root()
    logger.info(f"\nMerkle Root: {root}")
    logger.info(f"Current tree size: {tree.size}")
    logger.info(f"Current tree depth: {tree.approx_depth}")

    leaf_index_to_prove = 1
    leaf_data_to_prove = leaves_data[leaf_index_to_prove]
    proof = tree.get_proof(leaf_index_to_prove)
    logger.info(
        f"Proof for leaf '{leaf_data_to_prove}' at index {leaf_index_to_prove}: {proof}"
    )

    if MERKLELIB_AVAILABLE:
        is_valid = MerkleTree.verify_proof(root, leaf_data_to_prove, proof)
        logger.info(
            f"Proof verification for leaf '{leaf_data_to_prove}' is: {is_valid}"
        )
        assert is_valid, "Proof verification failed!"

        tampered_leaf_data = "transaction_A_details_123_TAMPERED"
        is_valid_tampered = MerkleTree.verify_proof(root, tampered_leaf_data, proof)
        logger.info(
            f"Proof verification for tampered leaf '{tampered_leaf_data}' is: {is_valid_tampered}"
        )
        assert not is_valid_tampered, "Tampered proof unexpectedly passed verification!"
    else:
        logger.warning("merklelib not installed; skipping verification tests.")

    logger.info("\n--- Testing Empty Tree ---")
    empty_tree = MerkleTree()
    try:
        empty_tree.get_root()
        assert False, "Should have raised MerkleTreeEmptyError for empty root"
    except MerkleTreeEmptyError as e:
        logger.info(f"Successfully caught expected error: {e}")

    try:
        empty_tree.get_proof(0)
        assert False, "Should have raised MerkleTreeEmptyError for empty proof"
    except MerkleTreeEmptyError as e:
        logger.info(f"Successfully caught expected error: {e}")

    logger.info(f"\n--- Testing Merkle Tree Persistence to {test_filepath} ---")
    await tree.save()
    loaded_tree = await MerkleTree.load()

    loaded_root = loaded_tree.get_root()
    logger.info(f"Loaded tree root: {loaded_root}")
    assert loaded_root == root, "Loaded tree root does not match original!"

    loaded_proof = loaded_tree.get_proof(leaf_index_to_prove)
    is_valid_loaded = MerkleTree.verify_proof(
        loaded_root, leaf_data_to_prove, loaded_proof
    )
    logger.info(
        f"Proof verification on loaded tree for leaf '{leaf_data_to_prove}' is: {is_valid_loaded}"
    )
    assert is_valid_loaded, "Proof verification failed on loaded tree!"

    logger.info("\n--- MerkleTree Example Usage Complete ---")

    if os.path.exists(test_storage_dir):
        shutil.rmtree(test_storage_dir, ignore_errors=True)
        logger.info(f"Cleaned up test directory: {test_storage_dir}")


if __name__ == "__main__":
    asyncio.run(main())
