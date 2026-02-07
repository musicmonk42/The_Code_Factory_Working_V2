# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Regulated-grade tests for audit_log/audit_backend_core.py

- No outbound network (KMS/alerts patched).
- Verifies: crypto/compression round-trip, tamper detection, retry/backoff,
  backend registry behavior, and strict crypto init.
- Robust: dynamically locates audit_backend_core.py anywhere in the repo.
"""

import asyncio
import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet, MultiFernet

# --- FIX: Clear Prometheus registry to prevent conflicts ---
from prometheus_client import REGISTRY

def _clear_prometheus_registry():
    """
    Clear all collectors from the Prometheus registry.
    This prevents conflicts when multiple test modules dynamically load
    audit_backend_core.py, which registers metrics on import.
    """
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            # Collector might not be registered, ignore
            pass

# Clear registry before loading audit_backend_core to ensure clean state
_clear_prometheus_registry()
# --- END FIX ---

# ---------------------------------------------------------------------------
# Locate audit_backend_core.py dynamically
# ---------------------------------------------------------------------------
THIS_FILE = Path(__file__).resolve()
# repo root ≈ .../generator
REPO_ROOT = THIS_FILE.parents[2] if len(THIS_FILE.parents) >= 2 else THIS_FILE.parent


def _find_core_file() -> Path:
    # Search for audit_backend_core.py anywhere under the repo root
    candidates = [p for p in REPO_ROOT.rglob("audit_backend_core.py") if p.is_file()]
    if not candidates:
        raise FileNotFoundError(
            f"Could not find audit_backend_core.py under {REPO_ROOT}"
        )
    # Prefer a path that contains '/audit_log/' in it
    for p in candidates:
        if "audit_log" in str(p).replace("\\", "/"):
            return p
    # Fallback to first candidate
    return candidates[0]


CORE_PATH = _find_core_file()

# CRITICAL: Set environment variables BEFORE importing the module
# to avoid validation errors at import time
import os

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

# Load module by file location using a stable module name so our patches match
spec = importlib.util.spec_from_file_location("audit_backend_core", CORE_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Could not load spec for {CORE_PATH}")
core = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
# Set required module attributes before exec_module
core.__path__ = []  # type: ignore[attr-defined]
core.__file__ = str(CORE_PATH)  # type: ignore[attr-defined]
spec.loader.exec_module(core)  # type: ignore[call-arg]

# Ensure the directory containing the module is importable for its relative imports
core_dir = CORE_PATH.parent
if str(core_dir) not in sys.path:
    sys.path.insert(0, str(core_dir))

# Re-export commonly used names from the loaded module for convenience
LogBackend = core.LogBackend
InMemoryBackend = core.InMemoryBackend
BackendNotFoundError = core.BackendNotFoundError
CryptoInitializationError = core.CryptoInitializationError
BACKEND_ERRORS = core.BACKEND_ERRORS
BACKEND_TAMPER_DETECTION_FAILURES = core.BACKEND_TAMPER_DETECTION_FAILURES
BACKEND_WRITES = core.BACKEND_WRITES
retry_operation = core.retry_operation
register_backend = core.register_backend
get_backend = core.get_backend

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cancel_backend_tasks(backend: LogBackend) -> None:
    """Cancel background tasks created by LogBackend to avoid leaks."""
    tasks = getattr(backend, "_async_tasks", set())
    for t in list(tasks):
        t.cancel()


def _counter_total_for_labels(counter, **expected_labels) -> float:
    """Sum counter samples that match expected label values."""
    total = 0.0
    for metric in counter.collect():
        for sample in metric.samples:
            # Only sum samples that end with _total (exclude _created timestamps)
            if not sample.name.endswith('_total'):
                continue
            labels = sample.labels or {}
            if all(labels.get(k) == v for k, v in expected_labels.items()):
                total += float(sample.value)
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
    This fixture runs automatically for every test.
    """
    # Force metric collection to ensure they're registered
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
    with patch("audit_backend_core.boto3.client") as mock_client:
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
