# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_audit_backend_railway_mode.py
"""
Tests for Railway/PaaS mode vs AWS KMS mode detection and handling in audit_backend_core.
Verifies that USE_ENV_SECRETS and AUDIT_CRYPTO_USE_KMS environment variables
correctly control whether KMS decryption is attempted in the audit backend.
"""

import base64
import os

import pytest


class TestIsRailwayOrPaaSModeFunction:
    """Tests for the _is_railway_or_paas_mode() helper function."""

    def test_use_env_secrets_true_returns_correct_values(self, monkeypatch):
        """Test that USE_ENV_SECRETS=true is correctly detected."""
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.delenv("AUDIT_CRYPTO_USE_KMS", raising=False)
        
        from generator.audit_log.audit_backend.audit_backend_core import _is_railway_or_paas_mode
        
        use_env_secrets, use_kms = _is_railway_or_paas_mode()
        assert use_env_secrets is True
        assert use_kms is True  # Default value

    def test_audit_crypto_use_kms_false_returns_correct_values(self, monkeypatch):
        """Test that AUDIT_CRYPTO_USE_KMS=false is correctly detected."""
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        monkeypatch.setenv("AUDIT_CRYPTO_USE_KMS", "false")
        
        from generator.audit_log.audit_backend.audit_backend_core import _is_railway_or_paas_mode
        
        use_env_secrets, use_kms = _is_railway_or_paas_mode()
        assert use_env_secrets is False
        assert use_kms is False

    def test_both_env_vars_set_returns_correct_values(self, monkeypatch):
        """Test that both env vars set correctly returns values."""
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.setenv("AUDIT_CRYPTO_USE_KMS", "false")
        
        from generator.audit_log.audit_backend.audit_backend_core import _is_railway_or_paas_mode
        
        use_env_secrets, use_kms = _is_railway_or_paas_mode()
        assert use_env_secrets is True
        assert use_kms is False

    def test_default_values_when_no_env_vars(self, monkeypatch):
        """Test default values when no env vars are set."""
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        monkeypatch.delenv("AUDIT_CRYPTO_USE_KMS", raising=False)
        
        from generator.audit_log.audit_backend.audit_backend_core import _is_railway_or_paas_mode
        
        use_env_secrets, use_kms = _is_railway_or_paas_mode()
        assert use_env_secrets is False
        assert use_kms is True  # Default is to use KMS
