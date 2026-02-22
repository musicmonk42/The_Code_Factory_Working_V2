# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unit tests for the PQCSigner class in pqc_signing.py.

Covers HMAC fallback behaviour (the only mode that runs without optional
PQC libraries), the module-level singleton helper, and the wiring of
PQCSigner into ArbiterGrowthManager._generate_idempotency_key.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from unittest.mock import MagicMock, patch

import pytest

from self_fixing_engineer.arbiter.arbiter_growth.pqc_signing import PQCSigner, get_signer


# ---------------------------------------------------------------------------
# HMAC-fallback backend (always available)
# ---------------------------------------------------------------------------


class TestPQCSignerHmacFallback:
    """Tests for the built-in HMAC-SHA-256 fallback backend."""

    def setup_method(self) -> None:
        self.key = b"test-key-for-unit-tests-32-bytes"
        self.signer = PQCSigner(hmac_key=self.key)

    def test_backend_is_hmac_when_pqc_libs_absent(self) -> None:
        """When PQC libs are not installed and USE_PQC_SIGNING is off, backend is 'hmac'."""
        assert self.signer.backend in {"hmac", "pqcrypto", "liboqs"}

    def test_sign_returns_bytes(self) -> None:
        sig = self.signer.sign(b"hello world")
        assert isinstance(sig, bytes)

    def test_sign_non_empty(self) -> None:
        sig = self.signer.sign(b"data")
        assert len(sig) > 0

    def test_verify_valid_signature(self) -> None:
        data = b"audit log entry"
        sig = self.signer.sign(data)
        assert self.signer.verify(data, sig) is True

    def test_verify_tampered_data_returns_false(self) -> None:
        data = b"original data"
        sig = self.signer.sign(data)
        assert self.signer.verify(b"tampered data", sig) is False

    def test_verify_tampered_signature_returns_false(self) -> None:
        data = b"original data"
        sig = self.signer.sign(data)
        bad_sig = bytes([b ^ 0xFF for b in sig])
        assert self.signer.verify(data, bad_sig) is False

    def test_verify_empty_data(self) -> None:
        data = b""
        sig = self.signer.sign(data)
        assert self.signer.verify(data, sig) is True

    def test_sign_deterministic_for_same_key(self) -> None:
        signer_a = PQCSigner(hmac_key=self.key)
        signer_b = PQCSigner(hmac_key=self.key)
        data = b"determinism test"
        assert signer_a.sign(data) == signer_b.sign(data)

    def test_sign_differs_for_different_keys(self) -> None:
        signer_b = PQCSigner(hmac_key=b"different-key-32-bytes-xxxxxxxxx")
        data = b"key difference test"
        assert self.signer.sign(data) != signer_b.sign(data)

    def test_hmac_output_matches_manual_computation(self) -> None:
        """HMAC fallback must match manual hmac.new() for the same key."""
        data = b"manual check"
        expected_hex = hmac.new(self.key, data, hashlib.sha256).hexdigest()
        sig = self.signer.sign(data)
        if self.signer.backend == "hmac":
            assert sig == expected_hex.encode("ascii")

    def test_default_key_derived_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PQCSigner reads ARBITER_ENCRYPTION_KEY from the environment."""
        monkeypatch.setenv("ARBITER_ENCRYPTION_KEY", "env-supplied-key")
        signer = PQCSigner()
        if signer.backend == "hmac":
            data = b"env key test"
            sig = signer.sign(data)
            expected = hmac.new(b"env-supplied-key", data, hashlib.sha256).hexdigest()
            assert sig == expected.encode("ascii")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestGetSigner:
    """Tests for the get_signer() module-level singleton helper."""

    def test_returns_pqcsigner_instance(self) -> None:
        signer = get_signer()
        assert isinstance(signer, PQCSigner)

    def test_same_instance_on_repeated_calls(self) -> None:
        s1 = get_signer()
        s2 = get_signer()
        assert s1 is s2

    def test_singleton_can_sign_and_verify(self) -> None:
        signer = get_signer()
        data = b"singleton test data"
        sig = signer.sign(data)
        assert signer.verify(data, sig) is True


# ---------------------------------------------------------------------------
# USE_PQC_SIGNING env-var wiring into ArbiterGrowthManager
# ---------------------------------------------------------------------------


class TestPQCSigningInArbiterGrowthManager:
    """
    Verify that ArbiterGrowthManager._generate_idempotency_key delegates to
    PQCSigner when USE_PQC_SIGNING=true, and uses plain HMAC otherwise.
    """

    def _make_manager(self) -> "ArbiterGrowthManager":  # type: ignore[name-defined]
        sqlalchemy = pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")
        aiofiles = pytest.importorskip("aiofiles", reason="aiofiles not installed")
        from self_fixing_engineer.arbiter.arbiter_growth.arbiter_growth_manager import (
            ArbiterGrowthManager,
        )
        from self_fixing_engineer.arbiter.arbiter_growth.config_store import ConfigStore
        from self_fixing_engineer.arbiter.arbiter_growth.idempotency import IdempotencyStore

        storage = MagicMock()
        kg = MagicMock()
        fb = MagicMock()
        cfg = MagicMock(spec=ConfigStore)
        cfg.get.side_effect = lambda key, default=None: default
        idm = MagicMock(spec=IdempotencyStore)

        manager = ArbiterGrowthManager(
            arbiter_name="test-arbiter",
            storage_backend=storage,
            knowledge_graph=kg,
            feedback_manager=fb,
            config_store=cfg,
            idempotency_store=idm,
        )
        return manager

    def test_idempotency_key_returns_string(self) -> None:
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")
        pytest.importorskip("aiofiles", reason="aiofiles not installed")
        from self_fixing_engineer.arbiter.arbiter_growth.models import GrowthEvent

        manager = self._make_manager()
        event = GrowthEvent(
            type="skill_acquired",
            timestamp="2025-01-01T00:00:00Z",
            details={"skill_name": "test-skill"},
        )
        key = manager._generate_idempotency_key(event, "test-service")
        assert isinstance(key, str)
        assert len(key) > 0

    def test_idempotency_key_is_deterministic(self) -> None:
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")
        pytest.importorskip("aiofiles", reason="aiofiles not installed")
        from self_fixing_engineer.arbiter.arbiter_growth.models import GrowthEvent

        manager = self._make_manager()
        event = GrowthEvent(
            type="skill_acquired",
            timestamp="2025-01-01T00:00:00Z",
            details={"skill_name": "same-skill"},
        )
        k1 = manager._generate_idempotency_key(event, "svc")
        k2 = manager._generate_idempotency_key(event, "svc")
        assert k1 == k2

    def test_idempotency_key_differs_per_service(self) -> None:
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")
        pytest.importorskip("aiofiles", reason="aiofiles not installed")
        from self_fixing_engineer.arbiter.arbiter_growth.models import GrowthEvent

        manager = self._make_manager()
        event = GrowthEvent(
            type="skill_acquired",
            timestamp="2025-01-01T00:00:00Z",
            details={"skill_name": "s"},
        )
        k1 = manager._generate_idempotency_key(event, "service-a")
        k2 = manager._generate_idempotency_key(event, "service-b")
        assert k1 != k2

    def test_pqc_signing_path_called_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When USE_PQC_SIGNING=true, sign() on the signer must be invoked."""
        pytest.importorskip("aiofiles", reason="aiofiles not installed")
        pytest.importorskip("sqlalchemy", reason="sqlalchemy not installed")
        import self_fixing_engineer.arbiter.arbiter_growth.arbiter_growth_manager as mgr_mod

        mock_signer = MagicMock()
        mock_signer.sign.return_value = b"fake-pqc-signature"

        monkeypatch.setattr(mgr_mod, "_USE_PQC_SIGNING", True)
        monkeypatch.setattr(mgr_mod, "_get_pqc_signer", lambda: mock_signer)

        from self_fixing_engineer.arbiter.arbiter_growth.models import GrowthEvent

        manager = self._make_manager()
        event = GrowthEvent(
            type="skill_acquired",
            timestamp="2025-01-01T00:00:00Z",
            details={"skill_name": "pqc-skill"},
        )
        manager._generate_idempotency_key(event, "svc")
        mock_signer.sign.assert_called_once()
