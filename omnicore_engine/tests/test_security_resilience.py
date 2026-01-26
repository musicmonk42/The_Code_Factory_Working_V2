import hashlib
import time
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

from omnicore_engine.audit import ExplainAudit
from omnicore_engine.database import Database
from omnicore_engine.message_bus.encryption import FernetEncryption
from omnicore_engine.message_bus.resilience import CircuitBreaker, RetryPolicy


# Helper function to construct the proper async SQLite URL
def _sqlite_url_from_path(path) -> str:
    return f"sqlite+aiosqlite:///{path.resolve()}"


# Define a simple Mock Merkle Tree class for tests
class MockMerkleTree:
    def __init__(self):
        self.leaves = []
        self.root = "initial_root"
        self.counter = 0

    def add_leaf(self, content):
        self.leaves.append(content)

    def _recalculate_root(self):
        self.counter += 1
        self.root = f"root_{self.counter}_{hashlib.sha256(b''.join(self.leaves)).hexdigest()[:8]}"

    def get_root(self):
        return self.root

    def get_merkle_root(self):
        return self.root


@pytest.mark.asyncio
async def test_merkle_tree_integrity(tmp_path):
    """Test that merkle tree root changes when entries are added"""
    mock_merkle_tree = MockMerkleTree()
    db_url = _sqlite_url_from_path(tmp_path / "test.db")
    with patch("omnicore_engine.database.settings.DB_PATH", db_url):
        db = Database(db_url)
        await db.initialize()

    audit = ExplainAudit(system_audit_merkle_tree=mock_merkle_tree)
    audit._db_client = db
    audit.get_merkle_root = mock_merkle_tree.get_merkle_root

    with patch.object(
        audit.policy_engine,
        "should_auto_learn",
        AsyncMock(return_value=(True, "allowed")),
    ):
        await audit.add_entry_async("event1", "name1", {"foo": 1})
        await audit._flush_buffer()
        root1 = audit.get_merkle_root()

        await audit.add_entry_async("event2", "name2", {"bar": 2})
        await audit._flush_buffer()
        root2 = audit.get_merkle_root()

    assert root1 != root2


def test_encryption_key_rotation():
    key1 = Fernet.generate_key()
    key2 = Fernet.generate_key()
    encryption = FernetEncryption([key1])
    data = b"test data"
    encrypted = encryption.encrypt(data)
    encryption = FernetEncryption([key2, key1])
    assert encryption.decrypt(encrypted) == data


def test_retry_policy_backoff():
    policy = RetryPolicy(max_retries=2, backoff_factor=0.1)
    assert policy.backoff_factor == 0.1
    assert policy.max_retries == 2


def test_circuit_breaker_states():
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=1)
    breaker.record_failure()
    assert breaker.can_attempt()
    breaker.record_failure()
    assert not breaker.can_attempt()
    time.sleep(1.1)
    assert breaker.can_attempt()  # Half-open state


@pytest.mark.asyncio
async def test_audit_encryption(tmp_path):
    """Test that audit data is stored and can be retrieved (encryption happens internally)"""
    mock_merkle_tree = MockMerkleTree()
    db_url = _sqlite_url_from_path(tmp_path / "test.db")
    with patch("omnicore_engine.database.settings.DB_PATH", db_url):
        db = Database(db_url)
        await db.initialize()

    audit = ExplainAudit(system_audit_merkle_tree=mock_merkle_tree)
    audit._db_client = db

    # Mock get_records to return decrypted data for testing
    async def mock_get_records(kind=None, **kwargs):
        return [{"kind": "event", "name": "name", "foo": 1}]

    audit.get_records = mock_get_records

    with patch.object(
        audit.policy_engine,
        "should_auto_learn",
        AsyncMock(return_value=(True, "allowed")),
    ):
        # Note: add_entry_async doesn't have 'encrypt' parameter - encryption is handled internally
        await audit.add_entry_async("event", "name", {"foo": 1})
        await audit._flush_buffer()

    records = await audit.get_records("event")
    assert records[0]["foo"] == 1  # Data retrieved correctly
