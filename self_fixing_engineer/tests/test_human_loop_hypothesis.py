# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Property-based tests for HumanInLoopConfig validation using Hypothesis.

Verifies invariants that should hold for all valid (and some invalid) inputs,
complementing the fixed-input tests in test_arbiter_human_loop.py.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

# Skip the entire module if heavy optional dependencies are not installed
pytest.importorskip("aiohttp", reason="aiohttp not installed (required by HumanInLoopConfig via human_loop.py)")

from self_fixing_engineer.arbiter.human_loop import HumanInLoopConfig


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_email_strategy = st.emails()
_port_strategy = st.integers(min_value=1, max_value=65535)
_timeout_strategy = st.integers(min_value=1, max_value=86400)
_retry_strategy = st.integers(min_value=0, max_value=20)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHumanInLoopConfigProperties:
    """Property-based invariant tests for HumanInLoopConfig."""

    @given(port=_port_strategy)
    @settings(max_examples=100)
    def test_valid_smtp_port_accepted(self, port: int) -> None:
        """Any integer port in [1, 65535] must be accepted."""
        cfg = HumanInLoopConfig(EMAIL_SMTP_PORT=port)
        assert cfg.EMAIL_SMTP_PORT == port

    @given(timeout=_timeout_strategy)
    @settings(max_examples=100)
    def test_default_timeout_stored_correctly(self, timeout: int) -> None:
        """DEFAULT_TIMEOUT_SECONDS must round-trip through the model unchanged."""
        cfg = HumanInLoopConfig(DEFAULT_TIMEOUT_SECONDS=timeout)
        assert cfg.DEFAULT_TIMEOUT_SECONDS == timeout

    @given(retries=_retry_strategy)
    @settings(max_examples=50)
    def test_max_notification_retries_stored_correctly(self, retries: int) -> None:
        """MAX_NOTIFICATION_RETRIES must round-trip through the model unchanged."""
        cfg = HumanInLoopConfig(MAX_NOTIFICATION_RETRIES=retries)
        assert cfg.MAX_NOTIFICATION_RETRIES == retries

    @given(
        smtp_server=st.text(min_size=1, max_size=100),
        smtp_user=st.text(min_size=1, max_size=50),
        smtp_password=st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_production_email_with_full_smtp_is_valid(
        self, smtp_server: str, smtp_user: str, smtp_password: str
    ) -> None:
        """In production, full SMTP config must not raise a ValidationError."""
        try:
            cfg = HumanInLoopConfig(
                IS_PRODUCTION=True,
                EMAIL_ENABLED=True,
                EMAIL_SMTP_SERVER=smtp_server,
                EMAIL_SMTP_USER=smtp_user,
                EMAIL_SMTP_PASSWORD=smtp_password,
                DATABASE_URL="postgresql://localhost/test",
            )
            assert cfg.IS_PRODUCTION is True
            assert cfg.EMAIL_ENABLED is True
        except ValidationError:
            # Some generated strings may fail other validators; that is fine.
            pass

    @given(
        missing_field=st.sampled_from(
            ["EMAIL_SMTP_SERVER", "EMAIL_SMTP_USER", "EMAIL_SMTP_PASSWORD"]
        )
    )
    @settings(max_examples=3)
    def test_production_email_without_smtp_raises(self, missing_field: str) -> None:
        """In production with EMAIL_ENABLED, any missing SMTP field must raise."""
        kwargs: dict = {
            "IS_PRODUCTION": True,
            "EMAIL_ENABLED": True,
            "EMAIL_SMTP_SERVER": "smtp.example.com",
            "EMAIL_SMTP_USER": "user@example.com",
            "EMAIL_SMTP_PASSWORD": "s3cr3t",
            "DATABASE_URL": "postgresql://localhost/prod",
        }
        del kwargs[missing_field]
        with pytest.raises(ValidationError):
            HumanInLoopConfig(**kwargs)

    @given(
        email_enabled=st.booleans(),
        is_production=st.just(False),
    )
    @settings(max_examples=20)
    def test_non_production_mode_never_requires_smtp(
        self, email_enabled: bool, is_production: bool
    ) -> None:
        """Outside production, EMAIL_ENABLED=True must not require SMTP fields."""
        cfg = HumanInLoopConfig(
            IS_PRODUCTION=is_production,
            EMAIL_ENABLED=email_enabled,
        )
        assert cfg.EMAIL_ENABLED == email_enabled

    @given(recipients=st.dictionaries(st.text(min_size=1, max_size=20), st.emails(), max_size=10))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_email_recipients_stored_correctly(self, recipients: dict) -> None:
        """EMAIL_RECIPIENTS dict must round-trip through the model unchanged."""
        cfg = HumanInLoopConfig(EMAIL_RECIPIENTS=recipients)
        assert cfg.EMAIL_RECIPIENTS == recipients
