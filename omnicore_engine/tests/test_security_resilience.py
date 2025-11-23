import time

import pytest
from cryptography.fernet import Fernet

from omnicore_engine.audit import ExplainAudit
from omnicore_engine.database import Database
from omnicore_engine.message_bus.encryption import FernetEncryption
from omnicore_engine.message_bus.resilience import CircuitBreaker, RetryPolicy


@pytest.mark.asyncio
async def test_merkle_tree_integrity(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    audit = ExplainAudit(db)
    await audit.add_entry_async("event1", "name1", {"foo": 1})
    root1 = audit.get_merkle_root()
    await audit.add_entry_async("event2", "name2", {"bar": 2})
    root2 = audit.get_merkle_root()
    assert root1 != root2
    await db.close()


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
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    audit = ExplainAudit(db)
    await audit.add_entry_async("event", "name", {"foo": 1}, encrypt=True)
    records = await audit.get_records("event")
    assert records[0]["foo"] == 1  # Decrypted correctly
    await db.close()
