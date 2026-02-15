# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the 5 bug fixes in the audit crypto subsystem:
- Bug 1: Rate limiting on auto-disable alert path
- Bug 2: Auto-recovery mechanism from auto-disable state
- Bug 3: Shared aiohttp session for alerts
- Bug 4: Circuit breaker for alert endpoint
- Bug 5: Placeholder endpoint warning
"""

import asyncio
import time
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest


# --- Bug 1 & 2 Tests: audit_crypto_ops.py ---

@pytest.mark.asyncio
async def test_bug1_rate_limiting_auto_disable_alerts():
    """Test that auto-disable alerts are rate-limited to once per 60 seconds."""
    from generator.audit_log.audit_crypto import audit_crypto_ops
    
    # Reset global state
    audit_crypto_ops._FALLBACK_ATTEMPT_COUNT = {"total": 0}
    audit_crypto_ops._LAST_AUTO_DISABLE_ALERT_TIME = 0
    audit_crypto_ops._FALLBACK_DISABLED_AT = 0
    
    # Mock dependencies
    with patch.object(audit_crypto_ops, "settings") as mock_settings, \
         patch.object(audit_crypto_ops, "crypto_provider_factory") as mock_factory, \
         patch.object(audit_crypto_ops, "send_alert") as mock_send_alert, \
         patch.object(audit_crypto_ops, "log_action") as mock_log_action, \
         patch.object(audit_crypto_ops, "time") as mock_time:
        
        # Configure mocks
        mock_settings.get.side_effect = lambda key, default=None: {
            "MAX_FALLBACK_ATTEMPTS_BEFORE_DISABLE": 10,
            "MAX_FALLBACK_ATTEMPTS_BEFORE_ALERT": 5,
            "FALLBACK_ALERT_INTERVAL_SECONDS": 300,
        }.get(key, default)
        mock_settings.PROVIDER_TYPE = "mock_provider"
        
        mock_provider = MagicMock()
        mock_provider.sign = AsyncMock(side_effect=Exception("Primary failed"))
        mock_factory.get_provider.return_value = mock_provider
        
        mock_send_alert.return_value = None
        mock_log_action.return_value = None
        
        # Simulate time progression
        current_time = 1000.0
        mock_time.time.return_value = current_time
        
        # Set counter to trigger auto-disable
        audit_crypto_ops._FALLBACK_ATTEMPT_COUNT["total"] = 20
        
        entry = {"action": "test", "entry_id": "123"}
        
        # First call should send alert
        with pytest.raises(audit_crypto_ops.CryptoOperationError):
            await audit_crypto_ops.safe_sign(entry, "key1", "hash1")
        
        # Verify alert was sent
        assert mock_send_alert.call_count == 1
        
        # Second call immediately after (within 60s) should NOT send alert
        mock_send_alert.reset_mock()
        with pytest.raises(audit_crypto_ops.CryptoOperationError):
            await audit_crypto_ops.safe_sign(entry, "key1", "hash1")
        
        # Verify alert was NOT sent (rate limited)
        assert mock_send_alert.call_count == 0
        
        # Third call after 60s should send alert again
        mock_send_alert.reset_mock()
        mock_time.time.return_value = current_time + 61
        with pytest.raises(audit_crypto_ops.CryptoOperationError):
            await audit_crypto_ops.safe_sign(entry, "key1", "hash1")
        
        # Verify alert was sent again
        assert mock_send_alert.call_count == 1


@pytest.mark.asyncio
async def test_bug2_auto_recovery_from_disabled_state():
    """Test that fallback auto-recovers after configurable cooldown period."""
    from generator.audit_log.audit_crypto import audit_crypto_ops
    
    # Reset global state
    audit_crypto_ops._FALLBACK_ATTEMPT_COUNT = {"total": 0}
    audit_crypto_ops._LAST_AUTO_DISABLE_ALERT_TIME = 0
    audit_crypto_ops._FALLBACK_DISABLED_AT = 0
    
    # Mock dependencies
    with patch.object(audit_crypto_ops, "settings") as mock_settings, \
         patch.object(audit_crypto_ops, "crypto_provider_factory") as mock_factory, \
         patch.object(audit_crypto_ops, "send_alert") as mock_send_alert, \
         patch.object(audit_crypto_ops, "log_action") as mock_log_action, \
         patch.object(audit_crypto_ops, "time") as mock_time, \
         patch.object(audit_crypto_ops, "logger") as mock_logger:
        
        # Configure mocks
        mock_settings.get.side_effect = lambda key, default=None: {
            "MAX_FALLBACK_ATTEMPTS_BEFORE_DISABLE": 10,
            "FALLBACK_AUTO_RECOVERY_SECONDS": 300,
        }.get(key, default)
        mock_settings.PROVIDER_TYPE = "mock_provider"
        
        mock_provider = MagicMock()
        mock_provider.sign = AsyncMock(return_value=b"success-signature")
        mock_factory.get_provider.return_value = mock_provider
        
        mock_send_alert.return_value = None
        mock_log_action.return_value = None
        
        # Simulate time progression
        current_time = 1000.0
        mock_time.time.return_value = current_time
        
        # Set counter to trigger auto-disable
        audit_crypto_ops._FALLBACK_ATTEMPT_COUNT["total"] = 20
        
        entry = {"action": "test", "entry_id": "123"}
        
        # First call should fail (disabled state)
        with pytest.raises(audit_crypto_ops.CryptoOperationError):
            await audit_crypto_ops.safe_sign(entry, "key1", "hash1")
        
        # Verify disabled timestamp was set
        assert audit_crypto_ops._FALLBACK_DISABLED_AT == current_time
        
        # Second call after 301 seconds should trigger recovery
        mock_time.time.return_value = current_time + 301
        signature = await audit_crypto_ops.safe_sign(entry, "key1", "hash1")
        
        # Verify recovery occurred
        assert audit_crypto_ops._FALLBACK_ATTEMPT_COUNT["total"] == 0
        assert audit_crypto_ops._FALLBACK_DISABLED_AT == 0
        assert signature == b"success-signature"
        
        # Verify warning log was called for recovery
        assert mock_logger.warning.called
        warning_call_args = mock_logger.warning.call_args_list
        recovery_warnings = [call for call in warning_call_args 
                            if len(call[0]) > 0 and "Auto-recovery" in call[0][0]]
        assert len(recovery_warnings) > 0


# --- Bug 3 & 4 Tests: audit_crypto_factory.py ---

@pytest.mark.asyncio
async def test_bug3_shared_session_reuse():
    """Test that aiohttp session is shared across multiple alert calls."""
    from generator.audit_log.audit_crypto import audit_crypto_factory
    
    # Reset global state
    await audit_crypto_factory._close_alert_session()  # Clean up any existing session
    audit_crypto_factory._alert_session = None
    audit_crypto_factory._alert_consecutive_failures = 0
    audit_crypto_factory._alert_circuit_open_until = 0
    audit_crypto_factory._last_placeholder_warning_time = 0
    
    # Mock dependencies - make retry_operation actually execute the function
    async def mock_retry(func, **kwargs):
        await func()
    
    with patch.object(audit_crypto_factory, "retry_operation", side_effect=mock_retry), \
         patch.object(audit_crypto_factory, "log_action") as mock_log_action, \
         patch.object(audit_crypto_factory, "_get_alert_session") as mock_get_session:
        
        # Create a mock session
        mock_session = MagicMock()
        mock_session.post = MagicMock()
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
        mock_session.post.return_value.__aexit__ = AsyncMock()
        mock_get_session.return_value = mock_session
        
        mock_log_action.return_value = None
        
        # Send first alert
        await audit_crypto_factory.send_alert("Alert 1", "critical")
        
        # Verify _get_alert_session was called
        assert mock_get_session.call_count == 1
        
        # Send second alert  
        await audit_crypto_factory.send_alert("Alert 2", "critical")
        
        # Verify _get_alert_session was called again (meaning it's being used for each call)
        assert mock_get_session.call_count == 2


@pytest.mark.asyncio
async def test_bug4_circuit_breaker_opens_after_failures():
    """Test that circuit breaker opens after 5 consecutive failures."""
    from generator.audit_log.audit_crypto import audit_crypto_factory
    
    # Reset global state
    audit_crypto_factory._alert_session = None
    audit_crypto_factory._alert_consecutive_failures = 0
    audit_crypto_factory._alert_circuit_open_until = 0
    audit_crypto_factory._last_placeholder_warning_time = 0
    
    # Mock dependencies
    with patch.object(audit_crypto_factory, "retry_operation") as mock_retry, \
         patch.object(audit_crypto_factory, "log_action") as mock_log_action, \
         patch.object(audit_crypto_factory, "logger") as mock_logger, \
         patch.object(audit_crypto_factory, "time") as mock_time, \
         patch.object(audit_crypto_factory, "_get_alert_session") as mock_get_session:
        
        # Simulate failure
        mock_retry.side_effect = Exception("Connection failed")
        mock_log_action.return_value = None
        
        current_time = 1000.0
        mock_time.time.return_value = current_time
        
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        
        # Send 5 alerts that fail
        for i in range(5):
            await audit_crypto_factory.send_alert(f"Alert {i}", "critical")
        
        # Verify failure counter increased
        assert audit_crypto_factory._alert_consecutive_failures == 5
        
        # Verify circuit was opened
        assert audit_crypto_factory._alert_circuit_open_until == current_time + 60
        
        # Verify warning was logged about circuit opening
        assert mock_logger.warning.called
        warning_call_args = mock_logger.warning.call_args_list
        circuit_warnings = [call for call in warning_call_args 
                           if len(call[0]) > 0 and "Opening circuit breaker" in call[0][0]]
        assert len(circuit_warnings) > 0
        
        # Reset mocks
        mock_retry.reset_mock()
        mock_retry.side_effect = None
        mock_retry.return_value = None
        
        # Try to send another alert immediately (circuit is open)
        await audit_crypto_factory.send_alert("Alert blocked", "critical")
        
        # Verify retry was NOT called (circuit breaker blocked it)
        assert mock_retry.call_count == 0


@pytest.mark.asyncio
async def test_bug4_circuit_breaker_resets_on_success():
    """Test that circuit breaker resets failure counter on successful alert."""
    from generator.audit_log.audit_crypto import audit_crypto_factory
    
    # Reset global state
    audit_crypto_factory._alert_session = None
    audit_crypto_factory._alert_consecutive_failures = 3
    audit_crypto_factory._alert_circuit_open_until = 0
    audit_crypto_factory._last_placeholder_warning_time = 0
    
    # Mock dependencies
    with patch.object(audit_crypto_factory, "retry_operation") as mock_retry, \
         patch.object(audit_crypto_factory, "log_action") as mock_log_action, \
         patch.object(audit_crypto_factory, "_get_alert_session") as mock_get_session:
        
        mock_retry.return_value = None
        mock_log_action.return_value = None
        
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        
        # Send successful alert
        await audit_crypto_factory.send_alert("Success", "critical")
        
        # Verify failure counter was reset
        assert audit_crypto_factory._alert_consecutive_failures == 0


# --- Bug 5 Tests: Placeholder endpoint warning ---

@pytest.mark.asyncio
async def test_bug5_placeholder_endpoint_warning():
    """Test that placeholder endpoint triggers rate-limited warning."""
    from generator.audit_log.audit_crypto import audit_crypto_factory
    
    # Reset global state
    audit_crypto_factory._alert_session = None
    audit_crypto_factory._alert_consecutive_failures = 0
    audit_crypto_factory._alert_circuit_open_until = 0
    audit_crypto_factory._last_placeholder_warning_time = 0
    
    # Mock dependencies
    with patch.object(audit_crypto_factory, "retry_operation") as mock_retry, \
         patch.object(audit_crypto_factory, "log_action") as mock_log_action, \
         patch.object(audit_crypto_factory, "logger") as mock_logger, \
         patch.object(audit_crypto_factory, "time") as mock_time, \
         patch.object(audit_crypto_factory, "_get_alert_session") as mock_get_session:
        
        mock_retry.return_value = None
        mock_log_action.return_value = None
        
        current_time = 1000.0
        mock_time.time.return_value = current_time
        
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        
        # Send alert to placeholder endpoint
        await audit_crypto_factory.send_alert(
            "Test", "critical", endpoint="http://localhost:8080/alert"
        )
        
        # Verify warning was logged
        assert mock_logger.warning.called
        warning_call_args = mock_logger.warning.call_args_list
        placeholder_warnings = [call for call in warning_call_args 
                               if len(call[0]) > 0 and "placeholder" in call[0][0]]
        assert len(placeholder_warnings) > 0
        
        # Reset mock
        mock_logger.reset_mock()
        
        # Send another alert immediately (should be rate-limited)
        await audit_crypto_factory.send_alert(
            "Test2", "critical", endpoint="http://localhost:8080/alert"
        )
        
        # Verify warning was NOT logged again (rate limited)
        warning_call_args = mock_logger.warning.call_args_list
        placeholder_warnings = [call for call in warning_call_args 
                               if len(call[0]) > 0 and "placeholder" in call[0][0]]
        assert len(placeholder_warnings) == 0
        
        # Send alert after 301 seconds
        mock_time.time.return_value = current_time + 301
        await audit_crypto_factory.send_alert(
            "Test3", "critical", endpoint="http://localhost:8080/alert"
        )
        
        # Verify warning was logged again
        warning_call_args = mock_logger.warning.call_args_list
        placeholder_warnings = [call for call in warning_call_args 
                               if len(call[0]) > 0 and "placeholder" in call[0][0]]
        assert len(placeholder_warnings) > 0
