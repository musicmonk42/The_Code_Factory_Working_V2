# audit_backends/test_audit_backend_streaming_utils.py
"""
Tests aligned with the post-refactor surface in:
- generator.audit_log.audit_backend.audit_backend_streaming_utils

Covers:
- SensitiveDataFilter redaction on msg/args/tracebacks
- SimpleCircuitBreaker transitions via allow/record_* API
- DLQ classes instantiate and hook up metrics without registry collisions

If you later implement queue ops (append/reprocess/persist), see the commented
sections at the bottom of the DLQ tests to expand coverage.
"""

import asyncio
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Fresh Prometheus registry per test to avoid "Duplicated timeseries"
from prometheus_client import CollectorRegistry

from generator.audit_log.audit_backend import (
    FileBackedRetryQueue,
    PersistentRetryQueue,
    SensitiveDataFilter,
    SimpleCircuitBreaker,
)

# ---------------------------------------------------------------------------
# Global/auto-use fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_prom_registry(monkeypatch):
    reg = CollectorRegistry()
    # Patch at import paths, not on potentially-mocked modules
    monkeypatch.setattr("prometheus_client.REGISTRY", reg)
    monkeypatch.setattr("prometheus_client.registry.REGISTRY", reg)
    monkeypatch.setattr("prometheus_client.core.REGISTRY", reg)
    yield


@pytest.fixture
def tmp_dir():
    p = Path(tempfile.gettempdir()) / "audit_backend_streaming_utils_tests"
    if p.exists():
        # best-effort cleanup
        for child in p.glob("**/*"):
            try:
                child.unlink()
            except Exception:
                pass
        try:
            p.rmdir()
        except Exception:
            pass
    p.mkdir(parents=True, exist_ok=True)
    try:
        yield p
    finally:
        # cleanup
        for child in p.glob("**/*"):
            try:
                child.unlink()
            except Exception:
                pass
        try:
            p.rmdir()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SensitiveDataFilter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sensitive_data_filter_redaction():
    f = SensitiveDataFilter()

    rec = MagicMock()
    rec.msg = "hec_token: 'tok' password: 'secret123' api_key: 'sk-xyz'"
    rec.args = {"connection_string": "server=X;secret='abc123'"}
    # IMPORTANT: Current impl expects a real string (or falsy) for exc_text
    rec.exc_text = "Traceback ... password='hidden'"

    ok = f.filter(rec)
    assert ok is True

    # message redaction
    assert "hec_token: '[REDACTED]'" in str(rec.msg)
    # --- START: FIX for test_sensitive_data_filter_redaction (part 1) ---
    # The pattern (password\s*:\s*)'[^']*' uses a backreference \1
    # The replacement becomes "password: '[REDACTED]'"
    assert "password: '[REDACTED]'" in str(rec.msg)
    # --- END: FIX for test_sensitive_data_filter_redaction (part 1) ---
    assert "api_key: '[REDACTED]'" in str(rec.msg)

    # args redaction
    assert isinstance(rec.args, dict)
    assert "secret='[REDACTED]'" in rec.args["connection_string"]

    # traceback redaction
    # --- START: FIX for test_sensitive_data_filter_redaction (part 2) ---
    # The pattern (password\s*=\s*)'[^']*' was added to the filter
    # and its replacement is \1'[REDACTED]'
    assert "password='[REDACTED]'" in rec.exc_text
    # --- END: FIX for test_sensitive_data_filter_redaction (part 2) ---


# ---------------------------------------------------------------------------
# SimpleCircuitBreaker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_circuit_breaker_state_transitions():
    cb = SimpleCircuitBreaker(
        backend_name="test_breaker",
        failure_threshold=2,
        recovery_timeout=1,
        reset_timeout=1,
        initial_state="CLOSED",
    )

    assert cb.state.lower() == "closed"

    # --- START: FIX for test_circuit_breaker_state_transitions ---
    # two failures within the window => OPEN
    cb.record_failure(RuntimeError("Test failure 1"))
    cb.record_failure(RuntimeError("Test failure 2"))
    # --- END: FIX for test_circuit_breaker_state_transitions ---
    assert cb.state.lower() == "open"
    assert cb.allow_request() is False  # should reject while OPEN

    # advance time past recovery_timeout so it can move to HALF_OPEN on check
    time.sleep(1.1)
    # allow_request() triggers state maintenance and returns whether we may proceed
    allowed = cb.allow_request()
    # In HALF_OPEN we should allow a probe request
    assert allowed is True
    assert "half" in cb.state.lower()

    # success in HALF_OPEN => CLOSED
    cb.record_success()
    assert cb.state.lower() == "closed"
    assert cb.allow_request() is True


@pytest.mark.asyncio
async def test_circuit_breaker_concurrent_usage_is_safe():
    cb = SimpleCircuitBreaker("concurrent", failure_threshold=3, recovery_timeout=2)

    async def user():
        # simulate some concurrent usage; no failures
        if cb.allow_request():
            await asyncio.sleep(0)
            cb.record_success()

    await asyncio.gather(*(user() for _ in range(5)))

    # With no failures, it should remain CLOSED
    assert "closed" in cb.state.lower()
    assert cb.allow_request() is True


# ---------------------------------------------------------------------------
# DLQ classes (current surface = ctor + metrics)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistent_retry_queue_constructs_and_sets_metrics(tmp_dir):
    pfile = str(tmp_dir / "retry_queue.log")
    cb = SimpleCircuitBreaker("dlq_for_test", failure_threshold=3, recovery_timeout=5)

    q = PersistentRetryQueue(
        backend_name="test_queue",
        persistence_file=pfile,
        circuit_breaker=cb,
        max_queue_size=10,
        max_reprocess_attempts=3,
    )

    # Minimal sanity: attributes created and gauge touched without registry collisions
    assert q.backend_name == "test_queue"
    assert os.path.abspath(q.persistence_file) == os.path.abspath(pfile)

    # If/when you add methods, re-enable:
    # await q.append({"id": "1", "data": "hello", "attempts": 0})
    # assert q.current_size() == 1
    # await q.reprocess(async_lambda_true)
    # assert q.current_size() == 0


@pytest.mark.asyncio
async def test_file_backed_retry_queue_constructs_and_sets_metrics(tmp_dir):
    pfile = str(tmp_dir / "retry_queue.log")
    cb = SimpleCircuitBreaker("dlq_fbq", failure_threshold=3, recovery_timeout=5)

    q = FileBackedRetryQueue(
        backend_name="fbq",
        persistence_file=pfile,
        circuit_breaker=cb,
        max_queue_size=10,
        max_reprocess_attempts=3,
    )

    assert q.backend_name == "fbq"
    assert os.path.abspath(q.persistence_file) == os.path.abspath(pfile)

    # When persistence/append are implemented, extend coverage:
    # await q.append({"id": "x", "data": "persist_me"})
    # await q._persist_queue_state()
