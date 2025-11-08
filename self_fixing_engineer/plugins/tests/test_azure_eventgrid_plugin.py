import os
import sys
import json
import logging
import asyncio
import time
import uuid
import pytest
import hashlib
from unittest.mock import MagicMock, patch, AsyncMock, Mock, PropertyMock
from typing import Dict, Any, List
import aiohttp

# Mock the core modules before any imports
# First, mock core_secrets
mock_secrets_manager = MagicMock()
mock_secrets_manager.get_secret = MagicMock(return_value="test_key")
mock_core_secrets = MagicMock()
mock_core_secrets.SecretsManager = MagicMock(return_value=mock_secrets_manager)
mock_core_secrets.SECRETS_MANAGER = mock_secrets_manager
sys.modules['core_secrets'] = mock_core_secrets
sys.modules['plugins.core_secrets'] = mock_core_secrets

# Mock core_audit
mock_audit_logger = MagicMock()
mock_audit_logger.log_event = MagicMock()
mock_core_audit = MagicMock()
mock_core_audit.AuditLogger = MagicMock(return_value=mock_audit_logger)
mock_core_audit.audit_logger = mock_audit_logger
sys.modules['core_audit'] = mock_core_audit
sys.modules['plugins.core_audit'] = mock_core_audit

# Mock core_utils - based on your actual core_utils.py structure
mock_alert_operator_instance = MagicMock()
mock_alert_operator_instance.alert = MagicMock()

def mock_alert_operator(message, level="INFO"):
    """Mock the alert_operator function that the plugin expects"""
    mock_alert_operator_instance.alert(message, level)

def mock_scrub_secrets(data):
    """Mock the scrub_secrets function (maps to scrub in actual core_utils)"""
    return data

mock_core_utils = MagicMock()
mock_core_utils.alert_operator = mock_alert_operator
mock_core_utils.scrub_secrets = mock_scrub_secrets
mock_core_utils.scrub = mock_scrub_secrets  # The actual function name
mock_core_utils.AlertOperator = MagicMock(return_value=mock_alert_operator_instance)
mock_core_utils.send_alert = MagicMock()
sys.modules['core_utils'] = mock_core_utils
sys.modules['plugins.core_utils'] = mock_core_utils

# Now we can safely import from the plugin
from plugins.azure_eventgrid_plugin.azure_eventgrid_plugin import (
    PRODUCTION_MODE, logger, NonCriticalError,
    PLUGIN_MANIFEST, AzureEventGridAuditHook, EventGridPermanentError,
    AnalyzerCriticalError
)

# --- Test Setup ---
@pytest.fixture(autouse=True)
def setup_logging():
    """Set up logging to capture output for tests."""
    logger.handlers = []
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield
    logger.handlers = []

@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all mocks before each test."""
    mock_alert_operator_instance.reset_mock()
    mock_audit_logger.reset_mock()
    mock_secrets_manager.reset_mock()
    mock_secrets_manager.get_secret.return_value = "test_key"  # Restore default
    yield

@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp.ClientSession with proper async context manager."""
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.closed = False
    
    # Create a proper async context manager for the response
    class MockResponse:
        def __init__(self, status=200, text="OK"):
            self.status = status
            self._text = text
            
        async def text(self):
            return self._text
            
        async def __aenter__(self):
            return self
            
        async def __aexit__(self, *args):
            pass
    
    # Create the mock post method that returns an async context manager
    mock_response = MockResponse()
    mock_post = MagicMock(return_value=mock_response)
    mock_session.post = mock_post
    
    async def mock_close():
        mock_session.closed = True
    mock_session.close = AsyncMock(side_effect=mock_close)
    
    with patch("aiohttp.ClientSession", return_value=mock_session):
        yield mock_session, mock_post, MockResponse

@pytest.fixture
def set_env(monkeypatch):
    """Fixture to set environment variables for tests."""
    def _set_env(vars: Dict[str, str]):
        for key, value in vars.items():
            monkeypatch.setenv(key, value)
    return _set_env

# --- Manifest Tests ---
def test_plugin_manifest_structure():
    """Test that the plugin manifest has all required fields."""
    required_fields = [
        "name", "version", "description", "entrypoint", "type", "author",
        "capabilities", "permissions", "dependencies", "min_core_version",
        "max_core_version", "health_check", "api_version", "license",
        "homepage", "tags", "is_demo_plugin", "signature"
    ]
    for field in required_fields:
        assert field in PLUGIN_MANIFEST, f"Manifest missing required field: {field}"
    assert PLUGIN_MANIFEST["name"] == "azure_eventgrid_plugin"
    assert PLUGIN_MANIFEST["is_demo_plugin"] is True
    assert PLUGIN_MANIFEST["type"] == "python"

# --- Production Mode Tests ---
def test_production_mode_block_missing_key(set_env, monkeypatch):
    """Test initialization fails in production if key missing."""
    set_env({"PRODUCTION_MODE": "true"})
    # Patch the module-level PRODUCTION_MODE variable
    monkeypatch.setattr("plugins.azure_eventgrid_plugin.azure_eventgrid_plugin.PRODUCTION_MODE", True)
    
    def mock_get_secret(key, **kwargs):
        # Return valid endpoints but no key
        if key == "EVENTGRID_ALLOWED_ENDPOINTS":
            return "https://valid.com"
        elif key == "AZURE_EVENTGRID_KEY":
            return None  # This should cause the failure
        return None
    
    mock_secrets_manager.get_secret.side_effect = mock_get_secret
    
    with pytest.raises(AnalyzerCriticalError, match="AZURE_EVENTGRID_KEY"):
        AzureEventGridAuditHook(endpoint_url="https://valid.com")

# --- Initialization Tests ---
@pytest.mark.asyncio
async def test_init_success(set_env):
    """Test successful initialization."""
    set_env({"PRODUCTION_MODE": "false"})
    mock_secrets_manager.get_secret.return_value = "test_key"
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost:8080/api/events")
    assert hook.endpoint_url == "http://localhost:8080/api/events"
    mock_audit_logger.log_event.assert_called_with(
        "eventgrid_hook_init",
        endpoint="http://localhost:8080/api/events",
        batch_size=10,
        flush_interval=5.0
    )
    await hook.close()

def test_init_invalid_endpoint_prod(set_env, monkeypatch):
    """Test invalid endpoint in production fails."""
    set_env({"PRODUCTION_MODE": "true"})
    # Patch the module-level PRODUCTION_MODE variable
    monkeypatch.setattr("plugins.azure_eventgrid_plugin.azure_eventgrid_plugin.PRODUCTION_MODE", True)
    
    def mock_get_secret(key, **kwargs):
        if key == "EVENTGRID_ALLOWED_ENDPOINTS":
            return "https://valid.com"
        elif key == "AZURE_EVENTGRID_KEY":
            return "test_key"
        return "test_key"
    
    mock_secrets_manager.get_secret.side_effect = mock_get_secret
    
    with pytest.raises(AnalyzerCriticalError, match="Non-HTTPS endpoint"):
        AzureEventGridAuditHook(endpoint_url="http://valid.com")
    
    mock_alert_operator_instance.alert.assert_called()
    assert "Non-HTTPS endpoint" in mock_alert_operator_instance.alert.call_args[0][0]

def test_init_not_in_allowlist_prod(set_env, monkeypatch):
    """Test endpoint not in allowlist in production fails."""
    set_env({"PRODUCTION_MODE": "true"})
    # Patch the module-level PRODUCTION_MODE variable
    monkeypatch.setattr("plugins.azure_eventgrid_plugin.azure_eventgrid_plugin.PRODUCTION_MODE", True)
    
    def mock_get_secret(key, **kwargs):
        if key == "EVENTGRID_ALLOWED_ENDPOINTS":
            return "https://allowed.com"
        elif key == "AZURE_EVENTGRID_KEY":
            return "test_key"
        return "test_key"
    
    mock_secrets_manager.get_secret.side_effect = mock_get_secret
    
    with pytest.raises(AnalyzerCriticalError, match="not in the allowed_endpoints list"):
        AzureEventGridAuditHook(endpoint_url="https://forbidden.com")
    
    mock_alert_operator_instance.alert.assert_called()
    assert "not in the allowed_endpoints list" in mock_alert_operator_instance.alert.call_args[0][0]

# --- Event Hook Tests ---
@pytest.mark.asyncio
async def test_audit_hook_success():
    """Test queuing an event successfully."""
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost")
    await hook.audit_hook("test_event", {"key": "value"})
    assert hook._event_queue.qsize() == 1
    
    # Get the event but don't remove it from queue
    event = await asyncio.wait_for(hook._event_queue.get(), timeout=1.0)
    hook._event_queue.task_done()
    
    # Check that the event was logged
    calls = [call for call in mock_audit_logger.log_event.call_args_list 
             if call[0][0] == "eventgrid_event_queued"]
    assert len(calls) > 0
    
    await hook.close()

@pytest.mark.asyncio
async def test_audit_hook_shutdown_drops_event():
    """Test dropping event during shutdown."""
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost")
    hook._shutdown_event.set()  # Simulate shutdown state
    await hook.audit_hook("dropped_event", {})
    mock_audit_logger.log_event.assert_any_call(
        "eventgrid_event_dropped",
        event_type="dropped_event",
        reason="shutting_down"
    )
    # Clean up the background task
    hook._sender_task.cancel()
    try:
        await hook._sender_task
    except asyncio.CancelledError:
        pass

# --- Send Batch Tests ---
@pytest.mark.asyncio
async def test_send_batch_success(mock_aiohttp_session):
    """Test successful batch send."""
    mock_session, mock_post, MockResponse = mock_aiohttp_session
    
    # Configure the mock response
    mock_response = MockResponse(status=200, text="OK")
    mock_post.return_value = mock_response
    
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost", session=mock_session)
    batch = [{"id": "1", "eventType": "test", "subject": "subj", "eventTime": "time", "data": {}, "dataVersion": "1.0", "signature": "sig"}]
    await hook._send_batch(batch)
    
    # Check that audit was logged for successful send
    calls = [call for call in mock_audit_logger.log_event.call_args_list 
             if call[0][0] == "eventgrid_batch_sent"]
    assert len(calls) > 0
    assert hook._sent_count == 1
    await hook.close()

@pytest.mark.asyncio
async def test_send_batch_retriable_failure(mock_aiohttp_session):
    """Test retriable failure with retry success."""
    mock_session, mock_post, MockResponse = mock_aiohttp_session
    
    # Create two different responses
    responses = [
        MockResponse(status=503, text="Service Unavailable"),
        MockResponse(status=200, text="OK")
    ]
    
    mock_post.side_effect = responses
    
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost", session=mock_session, retries=2, retry_backoff=0.1)
    batch = [{"id": "1", "eventType": "test", "subject": "subj", "eventTime": "time", "data": {}, "dataVersion": "1.0", "signature": "sig"}]
    await hook._send_batch(batch)
    
    assert mock_post.call_count == 2
    
    # Check for both failure and success audit logs
    failure_calls = [call for call in mock_audit_logger.log_event.call_args_list 
                     if call[0][0] == "eventgrid_batch_retriable_failure"]
    success_calls = [call for call in mock_audit_logger.log_event.call_args_list 
                     if call[0][0] == "eventgrid_batch_sent"]
    
    assert len(failure_calls) > 0
    assert len(success_calls) > 0
    
    await hook.close()

@pytest.mark.asyncio
async def test_send_batch_permanent_failure(mock_aiohttp_session):
    """Test permanent failure escalates."""
    mock_session, mock_post, MockResponse = mock_aiohttp_session
    
    mock_response = MockResponse(status=400, text="Bad Request")
    mock_post.return_value = mock_response
    
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost", session=mock_session)
    batch = [{"id": "1", "eventType": "test", "subject": "subj", "eventTime": "time", "data": {}, "dataVersion": "1.0", "signature": "sig"}]
    
    # The hook doesn't raise the error externally, it handles it internally
    await hook._send_batch(batch)
    
    # Check for permanent failure audit log
    failure_calls = [call for call in mock_audit_logger.log_event.call_args_list 
                     if call[0][0] == "eventgrid_batch_permanent_failure"]
    assert len(failure_calls) > 0
    
    await hook.close()

@pytest.mark.asyncio
async def test_send_batch_all_retries_fail(mock_aiohttp_session):
    """Test all retries fail escalates."""
    mock_session, mock_post, MockResponse = mock_aiohttp_session
    
    # All attempts will fail
    mock_response = MockResponse(status=503, text="Service Unavailable")
    mock_post.return_value = mock_response
    
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost", session=mock_session, retries=2, retry_backoff=0.1)
    batch = [{"id": "1", "eventType": "test", "subject": "subj", "eventTime": "time", "data": {}, "dataVersion": "1.0", "signature": "sig"}]
    
    await hook._send_batch(batch)
    
    assert mock_post.call_count == 2
    mock_alert_operator_instance.alert.assert_called()
    alert_args, _ = mock_alert_operator_instance.alert.call_args
    assert "Event Grid audit batch dropped after 2 attempts" in alert_args[0]
    await hook.close()

@pytest.mark.asyncio
async def test_send_batch_on_failure_callback(mock_aiohttp_session):
    """Test on_failure callback invoked on failure."""
    mock_session, mock_post, MockResponse = mock_aiohttp_session
    
    # Make the post method raise an exception
    mock_post.side_effect = aiohttp.ClientError("Network error")
    
    mock_on_failure = AsyncMock()
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost", session=mock_session, on_failure=mock_on_failure, retries=1)
    batch = [{"id": "1", "eventType": "test", "subject": "subj", "eventTime": "time", "data": {}, "dataVersion": "1.0", "signature": "sig"}]
    
    await hook._send_batch(batch)
    
    mock_on_failure.assert_called_once()
    call_args, _ = mock_on_failure.call_args
    assert call_args[0] == batch
    assert "Network error" in str(call_args[1])
    await hook.close()

# --- Batch Sender Tests ---
@pytest.mark.asyncio
async def test_batch_sender_success(mock_aiohttp_session):
    """Test _batch_sender collects and sends batches."""
    mock_session, mock_post, MockResponse = mock_aiohttp_session
    
    mock_response = MockResponse(status=200, text="OK")
    mock_post.return_value = mock_response
    
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost", session=mock_session, batch_size=2, flush_interval=0.1)
    
    await hook.audit_hook("event1", {})
    await hook.audit_hook("event2", {})
    
    await asyncio.sleep(0.5)  # Wait for flush
    assert hook._sent_count == 2
    
    await hook.close()

@pytest.mark.asyncio
async def test_batch_sender_shutdown_drains_queue(mock_aiohttp_session):
    """Test shutdown drains queue."""
    mock_session, mock_post, MockResponse = mock_aiohttp_session
    
    mock_response = MockResponse(status=200, text="OK")
    mock_post.return_value = mock_response
    
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost", session=mock_session, batch_size=10, flush_interval=10.0)
    await hook.audit_hook("event1", {})
    
    await hook.close()
    assert hook._sent_count == 1

# --- Sign Event Tests ---
@pytest.mark.asyncio
async def test_sign_event():
    """Test event signing."""
    mock_secrets_manager.get_secret.return_value = "hmac_key"
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost")
    event = {"key": "value"}
    sig = hook._sign_event(event)
    
    # Check the signature format (should be a hex string)
    assert isinstance(sig, str)
    assert len(sig) == 64  # SHA256 produces 64 hex characters
    
    await hook.close()

# --- Shutdown Tests ---
@pytest.mark.asyncio
async def test_close_own_session():
    """Test closing owned session."""
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost")
    await hook.close()
    # When the hook owns the session, it should create one during _send_batch
    # The test just checks that close() completes without error

@pytest.mark.asyncio
async def test_close_external_session(mock_aiohttp_session):
    """Test not closing external session."""
    mock_session, _, _ = mock_aiohttp_session
    mock_session.closed = False
    
    hook = AzureEventGridAuditHook(endpoint_url="http://localhost", session=mock_session)
    await hook.close()
    
    # External session should not be closed by the hook
    # The hook sets _own_session = False when a session is provided
    assert hook._own_session == False
    # The mock close() should not have been called
    mock_session.close.assert_not_called()

# --- Run Tests ---
if __name__ == "__main__":
    pytest.main(["-v", __file__])