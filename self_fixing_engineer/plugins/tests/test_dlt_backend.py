import os
import sys
import asyncio
import json
import logging
import pytest
import types
import re
import tempfile
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import dlt_backend

# --- Test Fixtures and Setup ---

@pytest.fixture(autouse=True)
def reset_clients_and_globals(monkeypatch):
    # Patch globals to ensure clean state for each test
    dlt_backend.off_chain_client = None
    dlt_backend.fabric_client = None
    dlt_backend.DLT_BACKEND_CONFIG = {}
    # Patch Redis client and OpenTelemetry
    monkeypatch.setattr(dlt_backend, "REDIS_CLIENT", None)
    monkeypatch.setattr(dlt_backend, "PRODUCTION_MODE", False)
    yield
    dlt_backend.off_chain_client = None
    dlt_backend.fabric_client = None

@pytest.fixture
def dummy_state():
    return {"value": 42, "timestamp": "2022-01-01T00:00:00Z", "metadata": {"foo": "bar"}}

@pytest.fixture
def dummy_checkpoint_manager(dummy_state):
    cm = dlt_backend.CheckpointManager(
        backend="dlt",
        enable_hash_chain=True,
        state_schema=None,
        encrypt_key=None
    )
    return cm

@pytest.fixture
def redis_mock(monkeypatch):
    class DummyRedis:
        def __init__(self):
            self.store = {}
        async def setnx(self, k, v):
            if k in self.store:
                return False
            self.store[k] = v
            return True
        async def expire(self, k, t):
            return True
        async def set(self, k, v, nx=None, ex=None):
            if nx and k in self.store:
                return False
            self.store[k] = v
            return True
        async def setex(self, k, ttl, v):
            self.store[k] = v
            return True
        async def get(self, k):
            return self.store.get(k)
        async def delete(self, k):
            self.store.pop(k, None)
            return True
    dummy = DummyRedis()
    monkeypatch.setattr(dlt_backend, "REDIS_CLIENT", dummy)
    return dummy

@pytest.fixture
def s3_and_fabric_dummy(monkeypatch):
    # Provide dummy S3 and Fabric clients
    monkeypatch.setattr(dlt_backend, "S3OffChainClient", dlt_backend.S3OffChainClient)
    monkeypatch.setattr(dlt_backend, "FabricClientWrapper", dlt_backend.FabricClientWrapper)

@pytest.fixture
def scrub_patch(monkeypatch):
    monkeypatch.setattr(dlt_backend, "scrub_sensitive_data", lambda d: d)

@pytest.fixture
def dummy_audit_logger(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(dlt_backend, "audit_logger", logger)
    return logger

@pytest.fixture
def dummy_alert_operator(monkeypatch):
    op = MagicMock()
    monkeypatch.setattr(dlt_backend, "alert_operator", op)
    return op

@pytest.fixture
def dummy_tracer(monkeypatch):
    class DummySpan:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def set_attribute(self, *a, **k): pass
        def record_exception(self, *a, **k): pass
        def set_status(self, *a, **k): pass
    class DummyTracer:
        def start_as_current_span(self, *a, **k): return DummySpan()
    monkeypatch.setattr(dlt_backend, "tracer", DummyTracer())
    return DummyTracer()

# --- Tests ---

@pytest.mark.asyncio
async def test_initialize_dlt_backend_success(monkeypatch, s3_and_fabric_dummy, dummy_audit_logger, dummy_alert_operator):
    config = {
        "off_chain_storage_type": "s3",
        "dlt_client_type": "fabric",
        "s3": {},
        "fabric": {},
    }
    await dlt_backend.initialize_dlt_backend(config)
    assert isinstance(dlt_backend.off_chain_client, dlt_backend.S3OffChainClient)
    assert isinstance(dlt_backend.fabric_client, dlt_backend.FabricClientWrapper)
    dummy_audit_logger.log_event.assert_called_with(
        "dlt_backend_initialized",
        off_chain_type="s3",
        dlt_type="fabric"
    )

@pytest.mark.asyncio
async def test_initialize_dlt_backend_offchain_fail(monkeypatch, dummy_alert_operator):
    class BadS3:
        async def health_check(self, correlation_id=None):
            return {"status": False, "message": "fail"}
    monkeypatch.setattr(dlt_backend, "S3OffChainClient", lambda cfg: BadS3())
    config = {"off_chain_storage_type": "s3", "dlt_client_type": "fabric", "s3": {}, "fabric": {}}
    with pytest.raises(dlt_backend.AnalyzerCriticalError):
        await dlt_backend.initialize_dlt_backend(config)
    dummy_alert_operator.assert_called()

@pytest.mark.asyncio
async def test_initialize_dlt_backend_dlt_fail(monkeypatch, s3_and_fabric_dummy, dummy_alert_operator):
    class BadFabric:
        def __init__(self, config, off_chain_client): pass
        async def health_check(self, correlation_id=None):
            return {"status": False, "message": "fail"}
    monkeypatch.setattr(dlt_backend, "FabricClientWrapper", BadFabric)
    config = {"off_chain_storage_type": "s3", "dlt_client_type": "fabric", "s3": {}, "fabric": {}}
    with pytest.raises(dlt_backend.AnalyzerCriticalError):
        await dlt_backend.initialize_dlt_backend(config)
    dummy_alert_operator.assert_called()

@pytest.mark.asyncio
async def test_save_and_load_cycle(monkeypatch, redis_mock, scrub_patch, dummy_audit_logger, dummy_alert_operator):
    await dlt_backend.initialize_dlt_backend({
        "off_chain_storage_type": "s3",
        "dlt_client_type": "fabric",
        "s3": {},
        "fabric": {}
    })
    cm = dlt_backend.CheckpointManager()
    state1 = {"value": 10, "timestamp": "2022-01-01T00:00:00Z", "metadata": {"foo": "bar"}}
    tx_id = await cm.save("test1", state1, metadata={"meta": "foo"})
    assert tx_id
    loaded = await cm.load("test1")
    assert loaded == state1

@pytest.mark.asyncio
async def test_save_idempotency(monkeypatch, redis_mock, scrub_patch):
    await dlt_backend.initialize_dlt_backend({
        "off_chain_storage_type": "s3",
        "dlt_client_type": "fabric",
        "s3": {},
        "fabric": {}
    })
    cm = dlt_backend.CheckpointManager()
    state = {"value": 1, "timestamp": "2022-01-01T00:00:00Z", "metadata": {"foo": "bar"}}
    tx1 = await cm.save("idempotent", state, metadata={})
    tx2 = await cm.save("idempotent", state, metadata={})
    assert tx1 == tx2  # no new tx for identical state

@pytest.mark.asyncio
async def test_rollback_and_diff(monkeypatch, redis_mock, scrub_patch):
    await dlt_backend.initialize_dlt_backend({
        "off_chain_storage_type": "s3",
        "dlt_client_type": "fabric",
        "s3": {},
        "fabric": {}
    })
    cm = dlt_backend.CheckpointManager()
    s1 = {"value": 1, "timestamp": "2022-01-01T00:00:00Z", "metadata": {"foo": "a"}}
    s2 = {"value": 2, "timestamp": "2022-01-02T00:00:00Z", "metadata": {"foo": "b"}}
    tx1 = await cm.save("rolltest", s1, metadata={})
    tx2 = await cm.save("rolltest", s2, metadata={})
    diff = await cm.diff("rolltest", 1, 2)
    assert isinstance(diff, dict)
    assert "value" in diff and diff["value"] == (1, 2)
    rollback_tx = await cm.rollback("rolltest", 1)
    assert isinstance(rollback_tx, dict)
    loaded = await cm.load("rolltest")
    assert loaded == s1

@pytest.mark.asyncio
async def test_hash_chain_integrity(monkeypatch, redis_mock, scrub_patch, dummy_audit_logger, dummy_alert_operator):
    # Tamper with the hash chain to cause failure
    await dlt_backend.initialize_dlt_backend({
        "off_chain_storage_type": "s3",
        "dlt_client_type": "fabric",
        "s3": {},
        "fabric": {}
    })
    cm = dlt_backend.CheckpointManager()
    s1 = {"value": 1, "timestamp": "2022-01-01T00:00:00Z", "metadata": {"foo": "a"}}
    s2 = {"value": 2, "timestamp": "2022-01-02T00:00:00Z", "metadata": {"foo": "b"}}
    await cm.save("tamper", s1, metadata={})
    await cm.save("tamper", s2, metadata={})
    # Tamper: monkeypatch the fabric_client to return wrong prev_hash
    orig_get_version_tx = dlt_backend.fabric_client.get_version_tx
    async def bad_get_version_tx(name, version, correlation_id=None):
        tx = await orig_get_version_tx(name, version, correlation_id)
        tx["metadata"]["hash"] = "bad_hash"
        return tx
    dlt_backend.fabric_client.get_version_tx = bad_get_version_tx
    with pytest.raises(dlt_backend.HashChainError):
        await cm.load("tamper", version=2)
    dummy_alert_operator.assert_called()

@pytest.mark.asyncio
async def test_off_chain_payload_corrupt(monkeypatch, redis_mock, scrub_patch, dummy_audit_logger, dummy_alert_operator):
    await dlt_backend.initialize_dlt_backend({
        "off_chain_storage_type": "s3",
        "dlt_client_type": "fabric",
        "s3": {},
        "fabric": {}
    })
    cm = dlt_backend.CheckpointManager()
    await cm.save("corrupt", {"value": 123, "timestamp": "2022-01-01T00:00:00Z", "metadata": {}}, metadata={})
    # Tamper with payload blob by monkeypatching the decompress function
    monkeypatch.setattr(dlt_backend, "decompress_json", lambda b: (_ for _ in ()).throw(ValueError("corrupt")))
    with pytest.raises(dlt_backend.AnalyzerCriticalError):
        await cm.load("corrupt")
    dummy_alert_operator.assert_called()

@pytest.mark.asyncio
async def test_async_retry_decorator_works(monkeypatch):
    calls = []
    @dlt_backend.async_retry(retries=3, delay=0.01, backoff=1)
    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("fail")
        return 42
    result = await flaky()
    assert result == 42
    assert len(calls) == 3

@pytest.mark.asyncio
async def test_maybe_sign_checkpoint_and_verify(monkeypatch):
    # Test _maybe_sign_checkpoint and HMAC verification
    key = "a" * 32
    monkeypatch.setenv("DLT_HMAC_KEY", key)
    monkeypatch.setattr(dlt_backend.SECRETS_MANAGER, "get_secret", lambda *a, **kw: key)
    data = {"name": "foo", "payload_hash": "abc", "prev_hash": "def"}
    sig = dlt_backend._maybe_sign_checkpoint(data)
    # Manually verify
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    import hmac, hashlib
    manual = hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    assert sig == manual

@pytest.mark.asyncio
async def test_encrypt_decrypt(monkeypatch):
    if not dlt_backend.HAVE_AESGCM:
        pytest.skip("cryptography not installed")
    key = os.urandom(32)
    plaintext = b"test123"
    cipher = dlt_backend.encrypt(plaintext, key)
    plain2 = dlt_backend.decrypt(cipher, key)
    assert plain2 == plaintext

@pytest.mark.asyncio
async def test_save_blob_max_size(monkeypatch):
    # Patch compress_json to return a huge blob
    monkeypatch.setattr(dlt_backend, "compress_json", lambda data: b"x" * (17 * 1024 * 1024))
    await dlt_backend.initialize_dlt_backend({
        "off_chain_storage_type": "s3",
        "dlt_client_type": "fabric",
        "s3": {},
        "fabric": {}
    })
    cm = dlt_backend.CheckpointManager()
    with pytest.raises(dlt_backend.AnalyzerCriticalError):
        await cm.save("huge", {"a": 1}, metadata={})

@pytest.mark.asyncio
async def test_name_validation(dummy_checkpoint_manager):
    with pytest.raises(ValueError):
        await dummy_checkpoint_manager.save("bad name !", {}, {})

def test_deep_diff():
    a = {"a": 1, "b": 2}
    b = {"a": 2, "c": 3}
    diff = dlt_backend._deep_diff(a, b)
    assert diff == {"a": (1,2), "b": (2, None), "c": (None,3)}

@pytest.mark.asyncio
async def test_unsupported_op(dummy_checkpoint_manager, dummy_alert_operator):
    with pytest.raises(NotImplementedError):
        await dlt_backend.dlt_backend(dummy_checkpoint_manager, "unknownop", "checkpoint1")
    dummy_alert_operator.assert_called()

@pytest.mark.asyncio
async def test_distributed_lock(redis_mock):
    # Test that lock works and releases
    async with dlt_backend._maybe_dist_lock("foo"):
        pass  # Should acquire lock and then release

@pytest.mark.asyncio
async def test_cache_fast_path(redis_mock, scrub_patch):
    await dlt_backend.initialize_dlt_backend({
        "off_chain_storage_type": "s3",
        "dlt_client_type": "fabric",
        "s3": {},
        "fabric": {}
    })
    cm = dlt_backend.CheckpointManager()
    s = {"value": 1, "timestamp": "2022-01-01T00:00:00Z", "metadata": {"foo": "bar"}}
    await cm.save("cachefast", s, metadata={})
    # Next load should hit cache
    loaded = await cm.load("cachefast")
    assert loaded == s