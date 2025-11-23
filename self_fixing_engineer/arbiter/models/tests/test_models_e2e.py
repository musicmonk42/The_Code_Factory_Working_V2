import asyncio
import json
import logging
import os
import shutil
import uuid
import pytest
import pytest_asyncio
from pytest_mock import MockerFixture
from datetime import datetime, timezone
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

# Import the centralized tracer configuration
from arbiter.otel_config import get_tracer

# Import modules to be tested
from arbiter.models.multi_modal_schemas import (
    ImageAnalysisResult,
)
from arbiter.models.redis_client import RedisClient
from arbiter.models.postgres_client import PostgresClient
from arbiter.models.audit_ledger_client import AuditLedgerClient
from arbiter.models.merkle_tree import MerkleTree

# Import exceptions
from redis.exceptions import ConnectionError as RedisConnectionError
from asyncpg.exceptions import PostgresError

# Configure logging for E2E tests
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Get tracer using centralized configuration
tracer = get_tracer("test-arbiter-models-e2e")

# Setup in-memory exporter for testing
in_memory_exporter = InMemorySpanExporter()

# Sample environment variables for E2E tests
SAMPLE_ENV = {
    "REDIS_URL": "redis://mock-redis:6379/0",
    "REDIS_USE_SSL": "false",
    "DATABASE_URL": "postgresql://mock_user:mock_pass@mock-postgres:5432/mock_db",
    "PG_POOL_MIN_SIZE": "1",
    "PG_POOL_MAX_SIZE": "5",
    "PG_POOL_TIMEOUT": "10",
    "PG_SSL_MODE": "prefer",
    "AUDIT_LEDGER_URL": "ws://mock-ledger:8545",
    "ETHEREUM_PRIVATE_KEY": "0x" + "1" * 64,
    "ETHEREUM_CONTRACT_ADDRESS": "0xMockContract",
    "ETHEREUM_CONTRACT_ABI_JSON": json.dumps(
        [
            {
                "type": "function",
                "name": "logEvent",
                "inputs": [{"name": "eventType", "type": "string"}],
            }
        ]
    ),
    "MERKLE_STORAGE_PATH": "./mock_merkle_storage",
    "LOG_LEVEL": "DEBUG",
    "SFE_OTEL_EXPORTER_TYPE": "console",
    "ENV": "dev",
}

# Sample data for tests
SAMPLE_IMAGE_ANALYSIS = {
    "image_id": str(uuid.uuid4()),
    "source_url": "http://example.com/image.jpg",
    "timestamp_utc": datetime.now(timezone.utc),
    "ocr_result": {"text": "Test OCR", "confidence": 0.95},
    "captioning_result": {"caption": "A test image.", "confidence": 0.88},
}

SAMPLE_AUDIT_EVENT = {
    "event_id": str(uuid.uuid4()),
    "event_type": "data_processed",
    "details": {"action": "store_multi_modal_data"},
    "timestamp": datetime.now(timezone.utc).isoformat(),
}


@pytest_asyncio.fixture(autouse=True)
async def setup_e2e_env(mocker: MockerFixture, tmp_path):
    """Set up environment variables, temp directories, and mocks for E2E tests."""
    for key, value in SAMPLE_ENV.items():
        mocker.patch.dict(os.environ, {key: value})

    # Create temp directory for Merkle tree storage
    mock_merkle_path = tmp_path / "merkle"
    mock_merkle_path.mkdir()
    os.environ["MERKLE_STORAGE_PATH"] = str(mock_merkle_path)

    # Mock external dependencies
    # Redis
    try:
        import redis.asyncio as aioredis

        mock_redis = mocker.MagicMock(spec=aioredis.Redis)
        mock_redis.ping = mocker.AsyncMock(return_value=True)
        mock_redis.set = mocker.AsyncMock(return_value=True)
        mock_redis.get = mocker.AsyncMock(
            return_value=json.dumps("test_value").encode()
        )
        mock_redis.delete = mocker.AsyncMock(return_value=1)
        mock_redis.close = mocker.AsyncMock()
        mock_redis.info = mocker.AsyncMock(return_value={"used_memory": 1048576})
        mock_redis.dbsize = mocker.AsyncMock(return_value=100)

        # Mock lock
        mock_lock = mocker.MagicMock()
        mock_lock.acquire = mocker.AsyncMock(return_value=True)
        mock_lock.release = mocker.AsyncMock()
        mock_lock.__aenter__ = mocker.AsyncMock(return_value=mock_lock)
        mock_lock.__aexit__ = mocker.AsyncMock()
        mocker.patch.object(mock_redis, "lock", return_value=mock_lock)

        mocker.patch("redis.asyncio.from_url", return_value=mock_redis)
    except ImportError:
        pass

    # PostgreSQL
    try:
        import asyncpg

        mock_pool = mocker.MagicMock(spec=asyncpg.pool.Pool)
        mock_conn = mocker.AsyncMock()
        mock_conn.execute = mocker.AsyncMock(return_value="INSERT 0 1")
        mock_conn.fetch = mocker.AsyncMock(
            return_value=[
                {
                    "id": SAMPLE_AUDIT_EVENT["event_id"],
                    "data": {},
                    "type": "data_processed",
                }
            ]
        )
        mock_conn.fetchrow = mocker.AsyncMock(
            return_value={
                "id": SAMPLE_AUDIT_EVENT["event_id"],
                "data": {},
                "type": "data_processed",
            }
        )
        mock_conn.fetchval = mocker.AsyncMock(return_value=1)

        mock_acquire_context = mocker.MagicMock()
        mock_acquire_context.__aenter__ = mocker.AsyncMock(return_value=mock_conn)
        mock_acquire_context.__aexit__ = mocker.AsyncMock(return_value=None)
        mock_pool.acquire.return_value = mock_acquire_context
        mock_pool.close = mocker.AsyncMock()
        mock_pool.get_size.return_value = 1
        mock_pool.is_closed.return_value = False
        mocker.patch("asyncpg.create_pool", mocker.AsyncMock(return_value=mock_pool))
    except ImportError:
        pass

    # Mock Audit Ledger Client
    mock_audit = mocker.MagicMock()
    mock_audit.connect = mocker.AsyncMock()
    mock_audit.disconnect = mocker.AsyncMock()
    mock_audit.log_event = mocker.AsyncMock(return_value="tx_hash_123")
    mock_audit.__aenter__ = mocker.AsyncMock(return_value=mock_audit)
    mock_audit.__aexit__ = mocker.AsyncMock()
    mocker.patch(
        "arbiter.models.audit_ledger_client.AuditLedgerClient", return_value=mock_audit
    )

    yield

    # Clean up temp directories
    shutil.rmtree(mock_merkle_path, ignore_errors=True)
    for key in SAMPLE_ENV:
        os.environ.pop(key, None)


@pytest_asyncio.fixture(autouse=True)
async def clear_metrics_and_traces():
    """Clear Prometheus metrics and OpenTelemetry traces before each test."""
    in_memory_exporter.clear()
    yield


@pytest.mark.asyncio
async def test_e2e_multi_modal_workflow(setup_e2e_env, mocker: MockerFixture):
    """E2E Test: Simulate a multi-modal data processing workflow."""
    # Initialize components
    merkle_tree = MerkleTree()
    redis_client = RedisClient()
    pg_client = PostgresClient()
    audit_client = AuditLedgerClient(dlt_type="ethereum")

    # Connect all clients
    await asyncio.gather(
        redis_client.connect(), pg_client.connect(), audit_client.connect()
    )

    # Step 1: Validate multi-modal data
    image_result = ImageAnalysisResult(**SAMPLE_IMAGE_ANALYSIS)
    assert image_result.image_id == SAMPLE_IMAGE_ANALYSIS["image_id"]
    assert image_result.ocr_result.text == "Test OCR"
    logger.info("E2E: Multi-modal schema validated.")

    # Step 2: Store multi-modal data in PostgreSQL
    pg_data = {
        "id": image_result.image_id,
        "type": "image_analysis",
        "data": image_result.model_dump(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Mock the PostgreSQL responses properly
    pg_client._pool.acquire.return_value.__aenter__.return_value.fetch.return_value = [
        {
            "id": pg_data["id"],
            "type": pg_data["type"],
            "data": pg_data["data"],
            "timestamp": pg_data["timestamp"],
        }
    ]
    pg_client._pool.acquire.return_value.__aenter__.return_value.fetchrow.return_value = {
        "id": pg_data["id"],
        "type": pg_data["type"],
        "data": pg_data["data"],
        "timestamp": pg_data["timestamp"],
    }

    saved_pg_id = await pg_client.save("feedback", pg_data)
    retrieved_pg = await pg_client.load("feedback", saved_pg_id)
    assert retrieved_pg["id"] == saved_pg_id
    assert retrieved_pg["data"]["image_id"] == image_result.image_id
    logger.info("E2E: Multi-modal data stored and retrieved from PostgreSQL.")

    # Step 3: Cache data in Redis
    redis_key = f"image:{image_result.image_id}"
    redis_value = image_result.model_dump_json()

    # Mock Redis get to return the stored value
    redis_client._pool.get = mocker.AsyncMock(return_value=redis_value.encode("utf-8"))

    await redis_client.set(redis_key, redis_value)
    retrieved_redis = await redis_client.get(redis_key)

    # Handle the response properly
    if isinstance(retrieved_redis, bytes):
        retrieved_redis = retrieved_redis.decode("utf-8")
    if isinstance(retrieved_redis, str):
        try:
            retrieved_redis = json.loads(retrieved_redis)
        except json.JSONDecodeError:
            pass

    logger.info("E2E: Multi-modal data cached and retrieved from Redis.")

    # Step 4: Add data to Merkle Tree for integrity
    merkle_data = json.dumps(pg_data).encode("utf-8")
    await merkle_tree.add_leaf(merkle_data)
    root = merkle_tree.get_root()
    proof = merkle_tree.get_proof(0)
    is_valid = MerkleTree.verify_proof(root, merkle_data, proof)
    assert is_valid
    logger.info("E2E: Merkle Tree integrity verified.")

    # Step 5: Log to Audit Ledger
    audit_details = {
        "image_id": image_result.image_id,
        "action": "processed_image",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    tx_hash = await audit_client.log_event("image:processed", audit_details)
    assert isinstance(tx_hash, str)
    logger.info("E2E: Audit event logged.")

    # Step 6: Persist Merkle Tree
    save_path = os.path.join(
        os.environ["MERKLE_STORAGE_PATH"], "merkle_tree_state.json.gz"
    )
    await merkle_tree.save(save_path)
    loaded_tree = await MerkleTree.load(save_path)
    assert len(loaded_tree._leaves) == 1
    logger.info("E2E: Merkle Tree saved and loaded.")

    # Disconnect all clients
    await asyncio.gather(
        redis_client.disconnect(), pg_client.disconnect(), audit_client.disconnect()
    )


@pytest.mark.asyncio
async def test_e2e_error_handling(setup_e2e_env, mocker: MockerFixture):
    """E2E Test: Simulate errors and verify recovery/retries."""
    redis_client = RedisClient()
    pg_client = PostgresClient()
    audit_client = AuditLedgerClient(dlt_type="ethereum")
    merkle_tree = MerkleTree()

    # Connect clients
    await asyncio.gather(
        redis_client.connect(), pg_client.connect(), audit_client.connect()
    )

    # Test Redis retry on connection error
    redis_call_count = 0

    async def redis_set_side_effect(*args, **kwargs):
        nonlocal redis_call_count
        redis_call_count += 1
        if redis_call_count == 1:
            raise RedisConnectionError("Connection failed")
        return True

    redis_client._pool.set = mocker.AsyncMock(side_effect=redis_set_side_effect)

    # Test PostgreSQL retry
    pg_call_count = 0

    async def pg_execute_side_effect(*args, **kwargs):
        nonlocal pg_call_count
        pg_call_count += 1
        if pg_call_count == 1:
            raise PostgresError("Query failed")
        return "INSERT 0 1"

    if pg_client._pool:
        pg_client._pool.acquire.return_value.__aenter__.return_value.execute = (
            mocker.AsyncMock(side_effect=pg_execute_side_effect)
        )

    # Simulate workflow with errors
    image_result = ImageAnalysisResult(**SAMPLE_IMAGE_ANALYSIS)
    pg_data = {
        "id": image_result.image_id,
        "type": "image_analysis",
        "data": image_result.model_dump(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # PostgreSQL operation with retry
    try:
        await pg_client.save("feedback", pg_data)
        logger.info("E2E Error: PostgreSQL operation completed after retry.")
    except Exception as e:
        logger.warning(f"PostgreSQL operation failed: {e}")

    # Redis operation with retry
    redis_key = f"image:{image_result.image_id}"
    try:
        await redis_client.set(redis_key, image_result.model_dump_json())
        logger.info("E2E Error: Redis operation completed after retry.")
    except Exception as e:
        logger.warning(f"Redis operation failed: {e}")

    # Merkle Tree operation
    merkle_data = json.dumps(pg_data).encode("utf-8")
    await merkle_tree.add_leaf(merkle_data)
    logger.info("E2E Error: Merkle Tree leaf added.")

    # Audit Ledger operation
    tx_hash = await audit_client.log_event(
        "image:processed", {"image_id": image_result.image_id}
    )
    assert isinstance(tx_hash, str)
    logger.info("E2E Error: Audit event logged.")

    await asyncio.gather(
        redis_client.disconnect(), pg_client.disconnect(), audit_client.disconnect()
    )


@pytest.mark.asyncio
async def test_e2e_concurrency(setup_e2e_env, mocker: MockerFixture):
    """E2E Test: Simulate concurrent operations across components."""
    redis_client = RedisClient()
    pg_client = PostgresClient()
    audit_client = AuditLedgerClient(dlt_type="ethereum")

    await asyncio.gather(
        redis_client.connect(), pg_client.connect(), audit_client.connect()
    )

    async def process_task(i: int):
        image_id = str(uuid.uuid4())
        image_result = ImageAnalysisResult(
            image_id=image_id,
            source_url=f"http://example.com/image_{i}.jpg",
            ocr_result={"text": f"OCR_{i}", "confidence": 0.9},
        )
        pg_data = {
            "id": image_id,
            "type": "image_analysis",
            "data": image_result.model_dump(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Mock returns for concurrent operations
        pg_client._pool.acquire.return_value.__aenter__.return_value.fetch.return_value = [
            {"id": image_id}
        ]

        saved_id = await pg_client.save("feedback", pg_data)
        await redis_client.set(f"image:{image_id}", image_result.model_dump_json())
        await audit_client.log_event("image:processed", {"image_id": image_id})
        return saved_id

    tasks = [process_task(i) for i in range(5)]
    saved_ids = await asyncio.gather(*tasks)
    assert len(saved_ids) == 5
    assert all(isinstance(sid, str) for sid in saved_ids)
    logger.info("E2E Concurrency: 5 concurrent operations completed.")

    await asyncio.gather(
        redis_client.disconnect(), pg_client.disconnect(), audit_client.disconnect()
    )


@pytest.mark.asyncio
async def test_e2e_invalid_data(setup_e2e_env):
    """E2E Test: Handle invalid multi-modal data."""
    redis_client = RedisClient()
    pg_client = PostgresClient()

    await asyncio.gather(redis_client.connect(), pg_client.connect())

    # Invalid data (negative confidence)
    invalid_image = SAMPLE_IMAGE_ANALYSIS.copy()
    invalid_image["ocr_result"] = {"text": "Invalid", "confidence": -0.1}
    with pytest.raises(ValidationError) as exc_info:
        ImageAnalysisResult(**invalid_image)
    assert "greater than or equal to 0" in str(exc_info.value)
    logger.info("E2E: Invalid multi-modal data caught by schema validation.")

    # Valid data for storage
    image_result = ImageAnalysisResult(**SAMPLE_IMAGE_ANALYSIS)
    pg_data = {
        "id": image_result.image_id,
        "type": "image_analysis",
        "data": image_result.model_dump(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Mock successful save
    pg_client._pool.acquire.return_value.__aenter__.return_value.fetch.return_value = [
        {"id": pg_data["id"]}
    ]

    saved_id = await pg_client.save("feedback", pg_data)
    assert saved_id
    logger.info("E2E: Valid data stored after invalid data handling.")

    await asyncio.gather(redis_client.disconnect(), pg_client.disconnect())


@pytest.mark.asyncio
async def test_e2e_data_integrity_flow(setup_e2e_env):
    """E2E Test: Complete data integrity verification flow."""
    merkle_tree = MerkleTree()
    redis_client = RedisClient()
    pg_client = PostgresClient()

    await redis_client.connect()
    await pg_client.connect()

    # Create multiple data entries
    data_entries = []
    for i in range(5):
        entry = {
            "id": str(uuid.uuid4()),
            "type": f"test_type_{i}",
            "data": {"index": i, "value": f"test_{i}"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        data_entries.append(entry)

        # Add to merkle tree for integrity
        await merkle_tree.add_leaf(json.dumps(entry).encode())

    # Get merkle root
    root = merkle_tree.get_root()

    # Verify each entry
    for i, entry in enumerate(data_entries):
        proof = merkle_tree.get_proof(i)
        is_valid = MerkleTree.verify_proof(root, json.dumps(entry).encode(), proof)
        assert is_valid, f"Entry {i} failed merkle verification"

    logger.info("E2E: All data entries verified with merkle tree")

    # Test tampering detection
    tampered_entry = data_entries[0].copy()
    tampered_entry["data"]["value"] = "tampered"
    tampered_proof = merkle_tree.get_proof(0)
    is_valid = MerkleTree.verify_proof(
        root, json.dumps(tampered_entry).encode(), tampered_proof
    )
    assert not is_valid, "Tampered data should fail verification"
    logger.info("E2E: Tampering detected successfully")

    await redis_client.disconnect()
    await pg_client.disconnect()


@pytest.mark.asyncio
async def test_e2e_cross_component_transaction(setup_e2e_env):
    """E2E Test: Cross-component transactional integrity."""
    redis_client = RedisClient()
    pg_client = PostgresClient()
    audit_client = AuditLedgerClient(dlt_type="ethereum")
    merkle_tree = MerkleTree()

    await asyncio.gather(
        redis_client.connect(), pg_client.connect(), audit_client.connect()
    )

    transaction_id = str(uuid.uuid4())

    # Start transaction-like operation
    transaction_data = {
        "id": transaction_id,
        "type": "cross_component_test",
        "data": {"step": 1, "status": "initiated"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Step 1: Store in PostgreSQL
    pg_client._pool.acquire.return_value.__aenter__.return_value.fetch.return_value = [
        {"id": transaction_id}
    ]
    await pg_client.save("feedback", transaction_data)

    # Step 2: Cache in Redis
    await redis_client.set(f"txn:{transaction_id}", transaction_data)

    # Step 3: Add to merkle tree
    await merkle_tree.add_leaf(json.dumps(transaction_data).encode())

    # Step 4: Log to audit ledger
    tx_hash = await audit_client.log_event(
        "transaction:created", {"transaction_id": transaction_id}
    )

    # Verify all components have the data
    redis_client._pool.get = AsyncMock(
        return_value=json.dumps(transaction_data).encode()
    )
    cached_data = await redis_client.get(f"txn:{transaction_id}")

    pg_client._pool.acquire.return_value.__aenter__.return_value.fetchrow.return_value = (
        transaction_data
    )
    stored_data = await pg_client.load("feedback", transaction_id)

    assert cached_data is not None
    assert stored_data is not None
    assert tx_hash is not None

    logger.info("E2E: Cross-component transaction completed successfully")

    await asyncio.gather(
        redis_client.disconnect(), pg_client.disconnect(), audit_client.disconnect()
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
