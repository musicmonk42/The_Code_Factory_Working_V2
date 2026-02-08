# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Regulated-grade tests for audit_log/audit_backend_core.py

- No outbound network (KMS/alerts patched).
- Verifies: crypto/compression round-trip, tamper detection, retry/backoff,
  backend registry behavior, and strict crypto init.
"""

# CRITICAL: Set environment variables BEFORE importing the module
# to avoid validation errors at import time
import os
from cryptography.fernet import Fernet

os.environ["AUDIT_LOG_DEV_MODE"] = "true"

# Dynaconf expects variables with "AUDIT_" prefix (envvar_prefix="AUDIT")
# Provide minimal valid configuration for testing
encryption_key = Fernet.generate_key().decode()
# Use @json prefix so dynaconf parses the string as JSON
os.environ["AUDIT_ENCRYPTION_KEYS"] = (
    f'@json [{{"key_id": "mock_test_key", "key": "{encryption_key}"}}]'
)
os.environ["AUDIT_COMPRESSION_ALGO"] = "zstd"
os.environ["AUDIT_COMPRESSION_LEVEL"] = "3"
os.environ["AUDIT_BATCH_FLUSH_INTERVAL"] = "5"
os.environ["AUDIT_BATCH_MAX_SIZE"] = "100"
os.environ["AUDIT_HEALTH_CHECK_INTERVAL"] = "60"
os.environ["AUDIT_RETRY_MAX_ATTEMPTS"] = "3"
os.environ["AUDIT_RETRY_BACKOFF_FACTOR"] = "0.5"
os.environ["AUDIT_TAMPER_DETECTION_ENABLED"] = "true"

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from cryptography.fernet import MultiFernet

# --- NOTE: Prometheus registry is NOT cleared ---
# Clearing the registry would unregister metrics, but the module-level 
# Counter objects would still reference the old (unregistered) metrics.
# Since we use `before`/`after` comparisons to check for metric increments,
# we don't need a clean registry - we just need consistent metrics.
from prometheus_client import REGISTRY
# --- END NOTE ---

# ---------------------------------------------------------------------------
# Standard imports using generator.audit_log path
# ---------------------------------------------------------------------------
# Add generator to path if needed
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import modules using standard paths
from generator.audit_log.audit_backend.audit_backend_core import (
    BACKEND_ERRORS,
    BACKEND_TAMPER_DETECTION_FAILURES,
    BACKEND_WRITES,
    BackendNotFoundError,
    CryptoInitializationError,
    LogBackend,
    InMemoryBackend,
    compute_hash,
    get_backend,
    register_backend,
    retry_operation,
    SCHEMA_VERSION,
    COMPRESSION_ALGO,
    COMPRESSION_LEVEL,
    ENCRYPTER,
    RETRY_BACKOFF_FACTOR,
    RETRY_MAX_ATTEMPTS,
    send_alert,
)

# Alias for convenience (used in monkeypatch calls)
core = sys.modules["generator.audit_log.audit_backend.audit_backend_core"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cancel_backend_tasks(backend: LogBackend) -> None:
    """Cancel background tasks created by LogBackend to avoid leaks."""
    tasks = getattr(backend, "_async_tasks", set())
    for t in list(tasks):
        t.cancel()


def _counter_total_for_labels(counter, **expected_labels) -> float:
    """Sum counter samples that match expected label values.

    Uses two strategies for robustness against Prometheus registry
    interference from other test modules:
      1. Try counter.collect() (the standard Prometheus API).
      2. Fall back to reading the counter's internal _metrics dict directly,
         which avoids dependency on registry state.
    """
    # --- Strategy 1: Standard collect() API ---
    total = 0.0
    try:
        for metric in counter.collect():
            for sample in metric.samples:
                # Only sum samples that end with _total (exclude _created timestamps)
                if not sample.name.endswith('_total'):
                    continue
                labels = sample.labels or {}
                if all(labels.get(k) == v for k, v in expected_labels.items()):
                    total += float(sample.value)
        if total > 0:
            return total
    except Exception:
        pass

    # --- Strategy 2: Direct internal access (fallback) ---
    # When the Prometheus registry is in an inconsistent state (e.g. after
    # another test module swaps/clears the global REGISTRY), collect() may
    # return empty samples even though the counter was incremented.  Reading
    # the internal _metrics dict bypasses the registry entirely.
    if hasattr(counter, '_metrics'):
        label_key = tuple(sorted(expected_labels.items()))
        for key, child in counter._metrics.items():
            if key == label_key:
                # Access the underlying value
                if hasattr(child, '_value'):
                    val = child._value
                    # _value may be a MmapedValue (multiprocess) or ValueClass
                    return float(val.get() if hasattr(val, 'get') else val)
    return total


# ---------------------------------------------------------------------------
# Event loop fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
async def ensure_metrics_work():
    """
    Ensure Prometheus metrics are properly initialized and captured for each test.

    When running the full test suite, other test modules (e.g. test_runner_metrics)
    may swap/clear the global Prometheus REGISTRY, which can disconnect the
    module-level Counter objects from the active registry.  This fixture
    re-fetches the live counter references from the core module and
    re-registers them with the current active REGISTRY if necessary.

    If the module was only partially loaded (e.g. due to an earlier import
    failure during pytest collection), the metrics are re-created using
    safe_counter to ensure tests can still run.
    """
    global BACKEND_ERRORS, BACKEND_WRITES, BACKEND_TAMPER_DETECTION_FAILURES

    # Re-fetch the counter objects from the live module to ensure we are
    # checking the same objects that the production code increments.
    live_module = sys.modules.get("generator.audit_log.audit_backend.audit_backend_core", core)

    # Defensively fetch metrics; if the module was partially loaded (e.g. during
    # early pytest collection before env vars were set), recreate them.
    from generator.audit_log.audit_backend.audit_backend_core import safe_counter
    BACKEND_ERRORS = getattr(live_module, "BACKEND_ERRORS", None) or safe_counter(
        "audit_backend_errors_total", "Total errors per backend", ["backend", "type"]
    )
    BACKEND_WRITES = getattr(live_module, "BACKEND_WRITES", None) or safe_counter(
        "audit_backend_writes_total", "Total writes to backend", ["backend"]
    )
    BACKEND_TAMPER_DETECTION_FAILURES = getattr(
        live_module, "BACKEND_TAMPER_DETECTION_FAILURES", None
    ) or safe_counter(
        "audit_backend_tamper_detection_failures_total",
        "Count of failed tamper detection checks",
        ["backend"],
    )

    # If the module was missing attributes, set them so other code can find them.
    if not hasattr(live_module, "BACKEND_ERRORS"):
        live_module.BACKEND_ERRORS = BACKEND_ERRORS
    if not hasattr(live_module, "BACKEND_WRITES"):
        live_module.BACKEND_WRITES = BACKEND_WRITES
    if not hasattr(live_module, "BACKEND_TAMPER_DETECTION_FAILURES"):
        live_module.BACKEND_TAMPER_DETECTION_FAILURES = BACKEND_TAMPER_DETECTION_FAILURES

    # Ensure the counters are registered with the current active REGISTRY.
    try:
        import prometheus_client
        active_registry = prometheus_client.REGISTRY
        for counter in (BACKEND_ERRORS, BACKEND_WRITES, BACKEND_TAMPER_DETECTION_FAILURES):
            try:
                active_registry.register(counter)
            except (ValueError, KeyError):
                pass  # Already registered – that's fine
    except Exception:
        pass

    # Force metric collection to warm up internal state
    _ = list(BACKEND_ERRORS.collect())
    _ = list(BACKEND_TAMPER_DETECTION_FAILURES.collect())
    _ = list(BACKEND_WRITES.collect())

    yield

    # Allow async tasks to complete and metrics to be incremented
    await asyncio.sleep(0.2)
    pending = [t for t in asyncio.all_tasks() if not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=2.0)


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def secure_encryption_env(monkeypatch):
    """Deterministic local MultiFernet + safe compression settings."""
    key = Fernet.generate_key()
    encrypter = MultiFernet([Fernet(key)])

    monkeypatch.setenv("AUDIT_LOG_DEV_MODE", "true")
    monkeypatch.setattr(core, "ENCRYPTER", encrypter, raising=False)

    if hasattr(core, "COMPRESSION_ALGO"):
        monkeypatch.setattr(core, "COMPRESSION_ALGO", "zstd", raising=False)
    if hasattr(core, "COMPRESSION_LEVEL"):
        monkeypatch.setattr(core, "COMPRESSION_LEVEL", 3, raising=False)

    yield encrypter


@pytest_asyncio.fixture
async def mock_send_alert(monkeypatch):
    """Patch send_alert so we can assert alerts without side effects."""
    mock = AsyncMock()
    if hasattr(core, "send_alert"):
        monkeypatch.setattr(core, "send_alert", mock, raising=False)
    return mock


@pytest_asyncio.fixture
async def kms_mock():
    """Patch boto3 KMS client if referenced during tests (keeps suite hermetic)."""
    with patch("generator.audit_log.audit_backend.audit_backend_core.boto3.client") as mock_client:
        kms = MagicMock()
        kms.decrypt.return_value = {"Plaintext": Fernet.generate_key()}
        mock_client.return_value = kms
        yield kms


@pytest_asyncio.fixture
async def test_backend(secure_encryption_env, mock_send_alert):
    """
    Concrete backend for exercising core behavior using in-memory storage.
    """

    class TestBackend(LogBackend):
        def __init__(self, params: Dict[str, Any]):
            self.name = "test-backend"
            self.storage: List[Dict[str, Any]] = []
            super().__init__(params)

        def _validate_params(self) -> None:
            return

        @asynccontextmanager
        async def _atomic_context(self, prepared_entries: List[Dict[str, Any]]):
            self.storage.extend(prepared_entries)
            yield

        async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
            self.storage.append(prepared_entry)

        async def _query_single(
            self, filters: Dict[str, Any], limit: int
        ) -> List[Dict[str, Any]]:
            results: List[Dict[str, Any]] = []
            for entry in reversed(self.storage):
                if all(entry.get(k) == v for k, v in filters.items()):
                    results.append(entry)
                if len(results) >= limit:
                    break
            return list(reversed(results))

        async def _migrate_schema(self) -> None:
            return

        async def _health_check(self) -> bool:
            return isinstance(self.storage, list)

        async def _get_current_schema_version(self) -> int:
            return getattr(core, "SCHEMA_VERSION", 1)

    backend = TestBackend(params={"env": "test"})
    try:
        yield backend
    finally:
        _cancel_backend_tasks(backend)
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_and_query_round_trip(test_backend):
    entry = {
        "event_type": "user_login",
        "actor": "user-123",
        "details": {"ip": "127.0.0.1"},
    }

    await test_backend.append(dict(entry))
    await test_backend.flush_batch()

    assert len(test_backend.storage) == 1
    stored = test_backend.storage[0]
    assert "entry_id" in stored
    assert "_audit_hash" in stored

    results = await test_backend.query({}, limit=10)
    assert len(results) == 1
    decoded = results[0]
    assert decoded["event_type"] == "user_login"
    assert decoded["actor"] == "user-123"
    assert "entry_id" in decoded
    assert "_audit_hash" in decoded


@pytest.mark.asyncio
async def test_tamper_detection_flags_and_skips(
    test_backend, monkeypatch, mock_send_alert
):
    await test_backend.append({"event_type": "x"})
    await test_backend.flush_batch()
    assert len(test_backend.storage) == 1

    backend_label = test_backend.__class__.__name__
    
    # Force metric collection before measuring
    _ = list(BACKEND_TAMPER_DETECTION_FAILURES.collect())
    
    before = _counter_total_for_labels(
        BACKEND_TAMPER_DETECTION_FAILURES, backend=backend_label
    )

    original_compute = core.compute_hash

    def evil_hash(_data: bytes) -> str:
        return "DELIBERATELY_WRONG_HASH"

    monkeypatch.setattr(core, "compute_hash", evil_hash)
    
    # Ensure tamper detection is explicitly enabled
    test_backend.tamper_detection_enabled = True

    results = await test_backend.query({}, limit=10)
    assert results == []

    # Give scheduled tasks (send_alert via create_task) more time to execute
    await asyncio.sleep(2.0)  # Increased from 1.0
    
    # Force all pending tasks to complete
    pending = [t for t in asyncio.all_tasks() if not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=2.0)

    # Force metric collection before assertion
    _ = list(BACKEND_TAMPER_DETECTION_FAILURES.collect())

    after = _counter_total_for_labels(
        BACKEND_TAMPER_DETECTION_FAILURES, backend=backend_label
    )
    assert after > before, f"Metric did not increment: before={before}, after={after}"

    if mock_send_alert is not None:
        assert mock_send_alert.await_count >= 1

    monkeypatch.setattr(core, "compute_hash", original_compute)


@pytest.mark.asyncio
async def test_retry_operation_respects_limits(monkeypatch):
    attempts = {"count": 0}

    async def failing_op():
        attempts["count"] += 1
        raise ValueError("expected failure")

    if hasattr(core, "RETRY_BACKOFF_FACTOR"):
        monkeypatch.setattr(core, "RETRY_BACKOFF_FACTOR", 0.001, raising=False)
    if hasattr(core, "RETRY_MAX_ATTEMPTS"):
        monkeypatch.setattr(core, "RETRY_MAX_ATTEMPTS", 3, raising=False)

    # Force metric collection before measuring
    _ = list(BACKEND_ERRORS.collect())
    
    before = _counter_total_for_labels(
        BACKEND_ERRORS, backend="TestBackend", type="ValueError"
    )

    with pytest.raises(ValueError, match="expected failure"):
        await retry_operation(
            failing_op,
            max_attempts=getattr(core, "RETRY_MAX_ATTEMPTS", 3),
            backoff_factor=getattr(core, "RETRY_BACKOFF_FACTOR", 0.001),
            backend_name="TestBackend",
            op_name="test_op",
        )

    assert attempts["count"] == getattr(core, "RETRY_MAX_ATTEMPTS", 3)

    # Give metrics more time to be collected  
    await asyncio.sleep(1.0)  # Increased from 0.5

    # FIX: Force all pending tasks to complete
    pending = [t for t in asyncio.all_tasks() if not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=1.0)

    # Force metric collection before assertion
    _ = list(BACKEND_ERRORS.collect())

    after = _counter_total_for_labels(
        BACKEND_ERRORS, backend="TestBackend", type="ValueError"
    )
    # The counter increments on EACH attempt, so with 3 max attempts we expect 3 increments
    assert after >= before + 3, f"Expected at least {before + 3} errors, got {after} (before={before})"


@pytest.mark.asyncio
async def test_inmemory_backend_basic_integration(
    secure_encryption_env, mock_send_alert
):
    backend = InMemoryBackend(params={"name": "inmemory-test"})
    try:
        await backend.append({"event_type": "ping"})
        await backend.flush_batch()
        results = await backend.query({}, limit=10)
        assert len(results) == 1
        assert results[0]["event_type"] == "ping"
    finally:
        _cancel_backend_tasks(backend)
        await asyncio.sleep(0)


def test_register_and_get_backend_round_trip():
    class DummyBackend(LogBackend):
        def _validate_params(self) -> None:
            return

        @asynccontextmanager
        async def _atomic_context(self, prepared_entries):
            yield

        async def _append_single(self, prepared_entry):
            return

        async def _query_single(self, filters, limit):
            return []

        async def _migrate_schema(self):
            return

        async def _health_check(self) -> bool:
            return True

        async def _get_current_schema_version(self) -> int:
            return getattr(core, "SCHEMA_VERSION", 1)

    register_backend("dummy-core-test", DummyBackend)
    backend = get_backend("dummy-core-test", params={})
    assert isinstance(backend, DummyBackend)

    with pytest.raises(BackendNotFoundError):
        get_backend("nonexistent-backend-type", params={})


def test_crypto_initialization_strictness(monkeypatch):
    class MinimalBackend(LogBackend):
        def _validate_params(self) -> None:
            return

        @asynccontextmanager
        async def _atomic_context(self, prepared_entries):
            yield

        async def _append_single(self, prepared_entry):
            return

        async def _query_single(self, filters, limit):
            return []

        async def _migrate_schema(self):
            return

        async def _health_check(self) -> bool:
            return True

        async def _get_current_schema_version(self) -> int:
            return getattr(core, "SCHEMA_VERSION", 1)

    monkeypatch.setattr(core, "ENCRYPTER", None, raising=False)
    with pytest.raises(CryptoInitializationError):
        MinimalBackend(params={})
