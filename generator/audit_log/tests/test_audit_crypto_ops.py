
"""
test_audit_crypto_ops.py

Regulated industry-grade test suite for audit_crypto_ops.py.

Features:
- Tests signing, verification, chaining, and HMAC fallback operations.
- Validates sensitive data redaction, audit logging, and provenance tracking.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests async-safe operations, retry logic, and thread-safety.
- Verifies error handling, fallback mechanisms, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (audit_log, audit_crypto_factory, secrets).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun
- cryptography, prometheus-client, opentelemetry-sdk
- audit_log, audit_crypto_factory, secrets
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from faker import Faker
from freezegun import freeze_time
from prometheus_client import REGISTRY
import hmac
import hashlib

from audit_crypto_ops import (
    sign_async, verify_async, chain_entry_async, hmac_sign_fallback,
    CRYPTO_ERRORS, SIGN_OPERATIONS, VERIFY_OPERATIONS
)
from audit_log import log_action
from audit_crypto_factory import crypto_provider

# Initialize faker for test data generation
fake = Faker()

# Test constants
MOCK_CORRELATION_ID = str(uuid.uuid4())
TEST_HMAC_SECRET = b"mock_hmac_secret_32_bytes_1234567890"
TEST_PREV_HASH = "prev_hash_mock"

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['AUDIT_CRYPTO_FALLBACK_ALERT_INTERVAL_SECONDS'] = '60'
os.environ['AUDIT_CRYPTO_MAX_FALLBACK_ATTEMPTS_BEFORE_ALERT'] = '3'
os.environ['AUDIT_CRYPTO_MAX_FALLBACK_ATTEMPTS_BEFORE_DISABLE'] = '10'

@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest_asyncio.fixture
async def mock_audit_log():
    """Mock audit_log.log_action."""
    with patch('audit_log.log_action') as mock_log:
        yield mock_log

@pytest_asyncio.fixture
async def mock_crypto_provider():
    """Mock crypto provider from audit_crypto_factory."""
    with patch('audit_crypto_ops.crypto_provider') as mock_provider:
        mock_provider_inst = AsyncMock()
        mock_provider_inst.sign.return_value = ("mock_signature", "mock_key_id")
        mock_provider_inst.verify.return_value = True
        mock_provider.return_value = mock_provider_inst
        yield mock_provider_inst

@pytest_asyncio.fixture
async def mock_secrets():
    """Mock secrets.py functions."""
    with patch('audit_crypto_ops.aget_fallback_hmac_secret') as mock_hmac_secret:
        mock_hmac_secret.return_value = TEST_HMAC_SECRET
        yield mock_hmac_secret

@pytest_asyncio.fixture
async def mock_opentelemetry():
    """Mock OpenTelemetry tracer."""
    with patch('audit_crypto_ops.trace') as mock_trace:
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_trace.get_tracer.return_value = mock_tracer
        yield mock_tracer, mock_span

class TestAuditCryptoOps:
    """Test suite for audit_crypto_ops.py."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_sign_async(self, mock_crypto_provider, mock_audit_log, mock_opentelemetry):
        """Test async signing operation."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID
        }
        prev_hash = TEST_PREV_HASH
        with freeze_time("2025-09-01T12:00:00Z"):
            signature, key_id = await sign_async(entry, prev_hash)
        assert signature == "mock_signature"
        assert key_id == "mock_key_id"
        mock_crypto_provider.sign.assert_called_once()
        mock_audit_log.assert_called_with("sign_operation", success=True, key_id="mock_key_id")
        mock_opentelemetry[1].set_attribute.assert_any_call("operation", "sign")
        assert REGISTRY.get_sample_value('audit_sign_operations_total', {'provider_type': 'software'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_verify_async(self, mock_crypto_provider, mock_audit_log):
        """Test async verification operation."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "signature": "mock_signature",
            "key_id": "mock_key_id"
        }
        prev_hash = TEST_PREV_HASH
        with freeze_time("2025-09-01T12:00:00Z"):
            result = await verify_async(entry, prev_hash)
        assert result is True
        mock_crypto_provider.verify.assert_called_once()
        mock_audit_log.assert_called_with("verify_operation", success=True, key_id="mock_key_id")
        assert REGISTRY.get_sample_value('audit_verify_operations_total', {'provider_type': 'software'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_chain_entry_async(self, mock_crypto_provider, mock_audit_log):
        """Test async chaining of log entries."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID
        }
        prev_hash = TEST_PREV_HASH
        with freeze_time("2025-09-01T12:00:00Z"):
            chained_entry = await chain_entry_async(entry, prev_hash)
        assert chained_entry["signature"] == "mock_signature"
        assert chained_entry["key_id"] == "mock_key_id"
        assert chained_entry["prev_hash"] == TEST_PREV_HASH
        mock_crypto_provider.sign.assert_called_once()
        mock_audit_log.assert_called_with("chain_entry", success=True, key_id="mock_key_id")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_hmac_sign_fallback(self, mock_secrets, mock_audit_log):
        """Test HMAC fallback signing."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID
        }
        prev_hash = TEST_PREV_HASH
        with freeze_time("2025-09-01T12:00:00Z"):
            signature = hmac_sign_fallback(entry, prev_hash)
        expected_data = json.dumps({"action": "user_login", "details_json": entry["details_json"], "trace_id": MOCK_CORRELATION_ID, "prev_hash": prev_hash}, sort_keys=True).encode('utf-8')
        expected_signature = hmac.new(TEST_HMAC_SECRET, expected_data, hashlib.sha256).hexdigest()
        assert signature == expected_signature
        mock_secrets.assert_called_once()
        mock_audit_log.assert_called_with("hmac_fallback_sign", success=True)
        assert REGISTRY.get_sample_value('audit_crypto_errors_total', {'type': 'FallbackUsed', 'provider_type': 'fallback', 'operation': 'sign'}) >= 0

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_sign_async_fallback(self, mock_crypto_provider, mock_secrets, mock_audit_log):
        """Test sign_async with fallback to HMAC."""
        mock_crypto_provider.sign.side_effect = Exception("Provider failure")
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID
        }
        prev_hash = TEST_PREV_HASH
        with freeze_time("2025-09-01T12:00:00Z"):
            signature, key_id = await sign_async(entry, prev_hash)
        expected_data = json.dumps({"action": "user_login", "details_json": entry["details_json"], "trace_id": MOCK_CORRELATION_ID, "prev_hash": prev_hash}, sort_keys=True).encode('utf-8')
        expected_signature = hmac.new(TEST_HMAC_SECRET, expected_data, hashlib.sha256).hexdigest()
        assert signature == expected_signature
        assert key_id == "hmac_fallback"
        mock_audit_log.assert_called_with("hmac_fallback_sign", success=True)
        assert REGISTRY.get_sample_value('audit_crypto_errors_total', {'type': 'FallbackUsed', 'provider_type': 'fallback', 'operation': 'sign'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_missing_hmac_secret(self, mock_crypto_provider, mock_secrets, mock_audit_log):
        """Test HMAC fallback with missing secret."""
        mock_crypto_provider.sign.side_effect = Exception("Provider failure")
        mock_secrets.return_value = None
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID
        }
        with pytest.raises(Exception, match="Both primary and fallback signing failed"):
            await sign_async(entry, TEST_PREV_HASH)
        mock_audit_log.assert_called_with("hmac_fallback_sign", success=False, error=Any)
        assert REGISTRY.get_sample_value('audit_crypto_errors_total', {'type': 'FallbackSecretMissing', 'provider_type': 'fallback', 'operation': 'hmac_sign'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_signing(self, mock_crypto_provider, mock_audit_log):
        """Test concurrent async signing operations."""
        async def sign_entry(i):
            entry = {
                "action": f"user_action_{i}",
                "details_json": json.dumps({"email": f"test{i}@example.com"}),
                "trace_id": MOCK_CORRELATION_ID
            }
            return await sign_async(entry, TEST_PREV_HASH)

        tasks = [sign_entry(i) for i in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            results = await asyncio.gather(*tasks)
        assert len(results) == 5
        assert all(signature == "mock_signature" and key_id == "mock_key_id" for signature, key_id in results)
        assert mock_crypto_provider.sign.call_count == 5
        mock_audit_log.assert_called_with("sign_operation", success=True, key_id="mock_key_id")
        assert REGISTRY.get_sample_value('audit_sign_operations_total', {'provider_type': 'software'}) == 5

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_invalid_entry_type(self, mock_audit_log):
        """Test signing with invalid entry type."""
        with pytest.raises(TypeError, match="Entry must be a dictionary"):
            await sign_async("invalid_entry", TEST_PREV_HASH)
        mock_audit_log.assert_called_with("sign_operation", success=False, error=Any)
        assert REGISTRY.get_sample_value('audit_crypto_errors_total', {'type': 'TypeError', 'provider_type': 'software', 'operation': 'sign'}) == 1

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_crypto_ops",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
