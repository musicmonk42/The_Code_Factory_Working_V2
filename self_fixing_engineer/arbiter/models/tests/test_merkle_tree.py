import asyncio
import json
import logging
import os
import gzip
import hashlib
from typing import List
import pytest
import pytest_asyncio
from pytest_mock import MockerFixture

# Import centralized OpenTelemetry configuration for testing
from arbiter.otel_config import get_tracer
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Import the MerkleTree client and its exceptions from the correct module
from merkle_tree import (
    MerkleTree,
    MerkleTreeEmptyError,
    MerkleProofError,
    MERKLE_OPS_TOTAL,
    MERKLE_TREE_SIZE,
    MERKLE_TREE_DEPTH,
    _write_compressed_json,
    _read_compressed_json,
)

# Configure logging for tests
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Setup for OpenTelemetry tracing with in-memory exporter for testing
in_memory_exporter = InMemorySpanExporter()

# Get tracer using centralized configuration
tracer = get_tracer(__name__)


@pytest_asyncio.fixture(autouse=True)
async def clear_metrics_and_traces():
    """Clear Prometheus metrics and OpenTelemetry traces before each test."""
    # Clear traces
    in_memory_exporter.clear()
    # Reset gauge metrics to 0
    try:
        MERKLE_TREE_SIZE.set(0)
        MERKLE_TREE_DEPTH.set(0)
    except:
        pass
    yield


@pytest_asyncio.fixture
async def merkle_tree():
    """Fixture for MerkleTree instance."""
    tree = MerkleTree()
    yield tree


def get_metric_value(metric, **labels):
    """Helper to get metric value with labels."""
    try:
        if labels:
            return metric.labels(**labels)._value.get()
        else:
            return metric._value.get()
    except:
        return 0


@pytest.mark.asyncio
async def test_initialization_success(merkle_tree):
    """Test successful initialization of MerkleTree."""
    assert len(merkle_tree._leaves) == 0
    # The _tree is created but with empty leaves
    assert merkle_tree._tree is not None
    assert merkle_tree.size == 0
    assert merkle_tree.approx_depth == 0
    assert get_metric_value(MERKLE_TREE_SIZE) == 0
    assert get_metric_value(MERKLE_TREE_DEPTH) == 0


@pytest.mark.asyncio
async def test_initialization_with_leaves():
    """Test initialization with initial leaves."""
    initial_leaves = [b"leaf1", b"leaf2"]
    tree_with_leaves = MerkleTree(leaves=initial_leaves)
    assert len(tree_with_leaves._leaves) == 2
    assert tree_with_leaves.size == 2
    # The depth of a tree with 2 leaves is 1
    assert tree_with_leaves.approx_depth == 1
    assert get_metric_value(MERKLE_TREE_SIZE) == 2
    assert get_metric_value(MERKLE_TREE_DEPTH) == 1


@pytest.mark.asyncio
async def test_initialization_with_store_raw():
    """Test initialization with store_raw option."""
    initial_leaves = [b"leaf1", b"leaf2"]
    tree_raw = MerkleTree(leaves=initial_leaves, store_raw=True)
    assert len(tree_raw._leaves) == 2
    assert tree_raw._store_raw is True
    # When store_raw=True, raw bytes are stored
    assert tree_raw._leaves[0] == b"leaf1"


@pytest.mark.asyncio
async def test_add_leaf_success(merkle_tree):
    """Test successful addition of a single leaf."""
    await merkle_tree.add_leaf("test_data")
    assert len(merkle_tree._leaves) == 1
    assert merkle_tree.size == 1
    assert get_metric_value(MERKLE_TREE_SIZE) == 1
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="add_leaf", status="success") == 1
    )
    spans = in_memory_exporter.get_finished_spans()
    add_span = next((span for span in spans if span.name == "merkle_add_leaf"), None)
    assert add_span is not None
    assert add_span.status.is_ok


@pytest.mark.asyncio
async def test_add_leaf_bytes(merkle_tree):
    """Test adding leaf as bytes."""
    await merkle_tree.add_leaf(b"test_bytes")
    assert len(merkle_tree._leaves) == 1
    assert merkle_tree.size == 1


@pytest.mark.asyncio
async def test_add_leaves_success(merkle_tree):
    """Test successful batch addition of leaves."""
    batch = ["data1", "data2", b"data3"]
    await merkle_tree.add_leaves(batch)
    assert len(merkle_tree._leaves) == 3
    assert merkle_tree.size == 3
    assert get_metric_value(MERKLE_TREE_SIZE) == 3
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="add_leaves", status="success")
        == 1
    )
    spans = in_memory_exporter.get_finished_spans()
    batch_span = next(
        (span for span in spans if span.name == "merkle_add_leaves"), None
    )
    assert batch_span is not None
    assert batch_span.attributes["merkle.num_leaves_added"] == 3
    assert batch_span.status.is_ok


@pytest.mark.asyncio
async def test_get_root_success(merkle_tree):
    """Test getting root for non-empty tree."""
    await merkle_tree.add_leaf("test_data")
    root = merkle_tree.get_root()
    assert isinstance(root, str)
    # SHA256 hex length is 64 characters
    assert len(root) == 64
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="get_root", status="success") == 1
    )


@pytest.mark.asyncio
async def test_get_root_empty_tree(merkle_tree):
    """Test getting root for empty tree raises error."""
    with pytest.raises(MerkleTreeEmptyError, match="Merkle tree is empty"):
        merkle_tree.get_root()
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="get_root", status="failure") == 1
    )


@pytest.mark.asyncio
async def test_get_proof_success(merkle_tree):
    """Test getting proof for valid index."""
    await merkle_tree.add_leaf("leaf1")
    await merkle_tree.add_leaf("leaf2")
    proof = merkle_tree.get_proof(0)
    assert isinstance(proof, list)
    # Check the structure of the proof items
    assert all(
        isinstance(item, dict) and "node" in item and "position" in item
        for item in proof
    )
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="get_proof", status="success") == 1
    )
    spans = in_memory_exporter.get_finished_spans()
    proof_span = next((span for span in spans if span.name == "merkle_get_proof"), None)
    assert proof_span is not None
    assert proof_span.attributes["merkle.proof_index"] == 0
    assert proof_span.status.is_ok


@pytest.mark.asyncio
async def test_get_proof_invalid_index(merkle_tree):
    """Test getting proof for invalid index."""
    await merkle_tree.add_leaf("leaf")
    with pytest.raises(IndexError, match="Leaf index.*out of bounds"):
        merkle_tree.get_proof(1)
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="get_proof", status="failure") == 1
    )


@pytest.mark.asyncio
async def test_get_proof_negative_index(merkle_tree):
    """Test getting proof for negative index."""
    await merkle_tree.add_leaf("leaf")
    with pytest.raises(IndexError, match="Leaf index.*out of bounds"):
        merkle_tree.get_proof(-1)
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="get_proof", status="failure") == 1
    )


@pytest.mark.asyncio
async def test_get_proof_empty_tree(merkle_tree):
    """Test getting proof for empty tree raises error."""
    with pytest.raises(
        MerkleTreeEmptyError, match="Attempted to get proof from an empty Merkle tree"
    ):
        merkle_tree.get_proof(0)
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="get_proof", status="failure") == 1
    )


@pytest.mark.asyncio
async def test_verify_proof_success(merkle_tree):
    """Test successful proof verification."""
    await merkle_tree.add_leaf("leaf1")
    await merkle_tree.add_leaf("leaf2")
    root = merkle_tree.get_root()
    proof = merkle_tree.get_proof(0)
    is_valid = MerkleTree.verify_proof(root, "leaf1", proof)
    assert is_valid
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="verify_proof", status="success")
        == 1
    )
    spans = in_memory_exporter.get_finished_spans()
    verify_span = next(
        (span for span in spans if span.name == "merkle_verify_proof"), None
    )
    assert verify_span is not None
    assert verify_span.status.is_ok


@pytest.mark.asyncio
async def test_verify_proof_with_bytes(merkle_tree):
    """Test proof verification with bytes input."""
    await merkle_tree.add_leaf(b"leaf_bytes")
    root = merkle_tree.get_root()
    proof = merkle_tree.get_proof(0)
    is_valid = MerkleTree.verify_proof(root, b"leaf_bytes", proof)
    assert is_valid


@pytest.mark.asyncio
async def test_verify_proof_tampered(merkle_tree):
    """Test verification failure for tampered leaf."""
    await merkle_tree.add_leaf("leaf1")
    await merkle_tree.add_leaf("leaf2")
    root = merkle_tree.get_root()
    proof = merkle_tree.get_proof(0)
    is_valid = MerkleTree.verify_proof(root, "tampered_leaf", proof)
    assert not is_valid
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="verify_proof", status="failure")
        == 1
    )


@pytest.mark.asyncio
async def test_verify_proof_malformed():
    """Test verification failure for malformed proof."""
    root = "a" * 64  # Valid hex string
    # Invalid hex in proof node
    with pytest.raises(MerkleProofError, match="Invalid hex string in proof node"):
        MerkleTree.verify_proof(
            root, "leaf", [{"node": "invalid_hex!", "position": "right"}]
        )
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="verify_proof", status="failure")
        == 1
    )


@pytest.mark.asyncio
async def test_verify_proof_invalid_position():
    """Test verification failure for invalid position."""
    root = "a" * 64
    with pytest.raises(MerkleProofError, match="Invalid proof node position"):
        MerkleTree.verify_proof(
            root, "leaf", [{"node": "b" * 64, "position": "invalid"}]
        )


@pytest.mark.asyncio
async def test_verify_proof_missing_fields():
    """Test verification failure for missing fields in proof."""
    root = "a" * 64
    with pytest.raises(MerkleProofError, match="Malformed proof node"):
        MerkleTree.verify_proof(root, "leaf", [{"node": "b" * 64}])  # Missing position


@pytest.mark.asyncio
async def test_save_success(merkle_tree, tmp_path):
    """Test successful tree save."""
    await merkle_tree.add_leaf("leaf1")
    await merkle_tree.add_leaf("leaf2")
    save_path = tmp_path / "merkle_tree_state.json.gz"
    await merkle_tree.save(str(save_path))
    assert os.path.exists(save_path)

    with gzip.open(save_path, "rt") as f:
        loaded_data = json.load(f)

    # The saved data should be a dict with version, store_raw, and leaves
    assert isinstance(loaded_data, dict)
    assert loaded_data.get("version") == 1
    assert loaded_data.get("store_raw") is False
    assert len(loaded_data.get("leaves", [])) == 2

    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="save_tree", status="success") == 1
    )
    spans = in_memory_exporter.get_finished_spans()
    save_span = next((span for span in spans if span.name == "merkle_save_tree"), None)
    assert save_span is not None
    assert save_span.attributes["merkle.save_path"] == str(save_path)
    assert save_span.status.is_ok


@pytest.mark.asyncio
async def test_save_with_store_raw(tmp_path):
    """Test saving tree with store_raw=True."""
    tree = MerkleTree(store_raw=True)
    await tree.add_leaf("raw_leaf")
    save_path = tmp_path / "merkle_raw.json.gz"
    await tree.save(str(save_path))

    with gzip.open(save_path, "rt") as f:
        loaded_data = json.load(f)

    assert loaded_data.get("store_raw") is True


@pytest.mark.asyncio
async def test_load_success(tmp_path):
    """Test successful tree load."""
    save_path = tmp_path / "merkle_tree_state.json.gz"

    # Create a valid saved file
    leaf1_hash = hashlib.sha256(b"leaf1").hexdigest()
    leaf2_hash = hashlib.sha256(b"leaf2").hexdigest()
    save_data = {"version": 1, "store_raw": False, "leaves": [leaf1_hash, leaf2_hash]}

    with gzip.open(save_path, "wt") as f:
        json.dump(save_data, f)

    loaded_tree = await MerkleTree.load(str(save_path))
    assert loaded_tree.size == 2
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="load_tree", status="success") == 1
    )
    spans = in_memory_exporter.get_finished_spans()
    load_span = next((span for span in spans if span.name == "merkle_load_tree"), None)
    assert load_span is not None
    assert load_span.attributes["merkle.load_path"] == str(save_path)
    assert load_span.status.is_ok


@pytest.mark.asyncio
async def test_load_legacy_format(tmp_path):
    """Test loading tree from legacy format (list instead of dict)."""
    save_path = tmp_path / "merkle_legacy.json.gz"

    # Create a legacy format file (just a list of hashes)
    leaf1_hash = hashlib.sha256(b"leaf1").hexdigest()
    leaf2_hash = hashlib.sha256(b"leaf2").hexdigest()

    with gzip.open(save_path, "wt") as f:
        json.dump([leaf1_hash, leaf2_hash], f)

    loaded_tree = await MerkleTree.load(str(save_path))
    assert loaded_tree.size == 2
    assert loaded_tree._store_raw is False


@pytest.mark.asyncio
async def test_load_file_not_found(tmp_path, caplog):
    """Test load when file not found initializes empty tree."""
    caplog.set_level(logging.WARNING)
    non_existent_path = tmp_path / "non_existent.json.gz"
    loaded_tree = await MerkleTree.load(str(non_existent_path))
    assert loaded_tree.size == 0
    assert "Merkle tree state file not found" in caplog.text
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="load_tree", status="failure") == 1
    )


@pytest.mark.asyncio
async def test_load_corrupted_file(tmp_path, caplog):
    """Test load with corrupted file initializes empty tree."""
    caplog.set_level(logging.ERROR)
    corrupted_path = tmp_path / "corrupted.json.gz"
    with open(corrupted_path, "wb") as f:
        f.write(b"invalid_gzip_data")
    loaded_tree = await MerkleTree.load(str(corrupted_path))
    assert loaded_tree.size == 0
    assert "is corrupted" in caplog.text
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="load_tree", status="failure") == 1
    )


@pytest.mark.asyncio
async def test_concurrent_add_leaves(merkle_tree):
    """Test concurrent addition of leaves."""

    async def add_leaves_task(batch: List[str]):
        await merkle_tree.add_leaves(batch)

    batches = [["data1", "data2"], ["data3", "data4"], ["data5"]]
    tasks = [add_leaves_task(batch) for batch in batches]
    await asyncio.gather(*tasks)
    assert merkle_tree.size == 5
    assert get_metric_value(MERKLE_TREE_SIZE) == 5
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="add_leaves", status="success")
        == 3
    )


@pytest.mark.asyncio
async def test_retry_on_save_file_error(merkle_tree, tmp_path, mocker: MockerFixture):
    """Test retry mechanism on save file error."""
    save_path = tmp_path / "merkle_tree_state.json.gz"

    # Mock the write function to fail twice then succeed
    original_write = _write_compressed_json
    call_count = [0]

    def mock_write(path, data):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise IOError("Write failed")
        return original_write(path, data)

    mocker.patch("merkle_tree._write_compressed_json", side_effect=mock_write)

    await merkle_tree.add_leaf("test_leaf")
    await merkle_tree.save(str(save_path))

    # The save should eventually succeed
    assert os.path.exists(save_path)
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="save_tree", status="success") == 1
    )


@pytest.mark.asyncio
async def test_retry_on_load_file_error(tmp_path, mocker: MockerFixture):
    """Test retry mechanism on load file error."""
    save_path = tmp_path / "merkle_tree_state.json.gz"

    # Create a valid file first
    save_data = {"version": 1, "store_raw": False, "leaves": []}
    with gzip.open(save_path, "wt") as f:
        json.dump(save_data, f)

    # Mock the read function to fail twice then succeed
    original_read = _read_compressed_json
    call_count = [0]

    def mock_read(path):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise IOError("Read failed")
        return original_read(path)

    mocker.patch("merkle_tree._read_compressed_json", side_effect=mock_read)

    loaded_tree = await MerkleTree.load(str(save_path))
    assert loaded_tree.size == 0
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="load_tree", status="success") == 1
    )


@pytest.mark.asyncio
async def test_save_load_roundtrip(merkle_tree, tmp_path):
    """Test save and load roundtrip preserves tree state."""
    # Add some leaves
    test_data = ["leaf1", "leaf2", "leaf3"]
    for data in test_data:
        await merkle_tree.add_leaf(data)

    original_root = merkle_tree.get_root()

    # Save the tree
    save_path = tmp_path / "merkle_roundtrip.json.gz"
    await merkle_tree.save(str(save_path))

    # Load the tree
    loaded_tree = await MerkleTree.load(str(save_path))

    # Verify the loaded tree has the same state
    assert loaded_tree.size == len(test_data)
    assert loaded_tree.get_root() == original_root

    # Verify proofs still work
    proof = loaded_tree.get_proof(0)
    is_valid = MerkleTree.verify_proof(loaded_tree.get_root(), "leaf1", proof)
    assert is_valid


@pytest.mark.asyncio
async def test_large_batch_with_offload_threshold(mocker: MockerFixture):
    """Test batch addition with hash offload threshold."""
    # Set a low threshold for testing
    mocker.patch.dict(os.environ, {"MERKLE_HASH_OFFLOAD": "2"})

    tree = MerkleTree()
    # Add more leaves than the threshold
    large_batch = [f"data_{i}" for i in range(5)]
    await tree.add_leaves(large_batch)

    assert tree.size == 5
    assert (
        get_metric_value(MERKLE_OPS_TOTAL, operation="add_leaves", status="success")
        == 1
    )


@pytest.mark.asyncio
async def test_properties(merkle_tree):
    """Test tree properties."""
    # Empty tree
    assert merkle_tree.size == 0
    assert merkle_tree.approx_depth == 0

    # Add one leaf
    await merkle_tree.add_leaf("leaf1")
    assert merkle_tree.size == 1
    assert merkle_tree.approx_depth == 0

    # Add more leaves
    await merkle_tree.add_leaf("leaf2")
    assert merkle_tree.size == 2
    assert merkle_tree.approx_depth == 1

    await merkle_tree.add_leaves(["leaf3", "leaf4"])
    assert merkle_tree.size == 4
    assert merkle_tree.approx_depth == 2
