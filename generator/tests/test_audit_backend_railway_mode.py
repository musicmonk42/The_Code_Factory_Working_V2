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

    def test_use_env_secrets_true_with_kms_default(self, monkeypatch):
        """Test that USE_ENV_SECRETS=true is correctly detected with default KMS setting."""
        monkeypatch.setenv("USE_ENV_SECRETS", "true")
        monkeypatch.delenv("AUDIT_CRYPTO_USE_KMS", raising=False)
        
        from generator.audit_log.audit_backend.audit_backend_core import _is_railway_or_paas_mode
        
        use_env_secrets, use_kms = _is_railway_or_paas_mode()
        assert use_env_secrets is True
        assert use_kms is True  # Default value

    def test_audit_crypto_use_kms_false_with_env_secrets_default(self, monkeypatch):
        """Test that AUDIT_CRYPTO_USE_KMS=false is correctly detected with default USE_ENV_SECRETS."""
        monkeypatch.delenv("USE_ENV_SECRETS", raising=False)
        monkeypatch.setenv("AUDIT_CRYPTO_USE_KMS", "false")
        
        from generator.audit_log.audit_backend.audit_backend_core import _is_railway_or_paas_mode
        
        use_env_secrets, use_kms = _is_railway_or_paas_mode()
        assert use_env_secrets is False
        assert use_kms is False

    def test_railway_mode_with_both_env_vars_set(self, monkeypatch):
        """Test Railway/PaaS mode detection with both USE_ENV_SECRETS=true and AUDIT_CRYPTO_USE_KMS=false."""
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
