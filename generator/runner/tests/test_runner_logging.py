
# test_runner_logging.py
# Industry-grade test suite for runner_logging.py, ensuring compliance with regulated standards.
# Covers unit and integration tests for logging features, with traceability, reproducibility, and security.

import pytest
import asyncio
import json
import os
import base64
import logging
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone
import uuid
from collections import deque

# Import required classes and functions from runner_logging
from runner.logging import (
    RedactionFilter, SigningFilter, EncryptionFilter, StructuredJSONFormatter,
    configure_logging_from_config, log_action, search_logs,
    LOG_HISTORY, PII_PATTERNS, HAS_ECDSA
)
from runner.config import RunnerConfig, SecretStr
from runner.utils import redact_secrets, encrypt_log, decrypt_log
from runner.errors import RunnerError, ConfigurationError, ExporterError, PersistenceError
from runner.errors import ERROR_CODE_REGISTRY as error_codes

# Configure logging for traceability and auditability
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Mock OpenTelemetry tracer for testing without external dependencies
class MockSpan:
    def set_attribute(self, key, value): pass
    def set_status(self, status): pass
    def record_exception(self, exception): pass
    def end(self): pass
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): pass

class MockTracer:
    def start_as_current_span(self, name, *args, **kwargs): return MockSpan()

mock_tracer = MockTracer()

# Fixture for temporary directory
@pytest.fixture
def tmp_path(tmp_path_factory):
    """Create a temporary directory for test files."""
    return tmp_path_factory.mktemp("logging_test")

# Fixture for mock OpenTelemetry tracer
@pytest.fixture(autouse=True)
def mock_opentelemetry():
    """Mock OpenTelemetry tracer for all tests."""
    with patch('runner.logging.trace', mock_tracer):
        yield

# Fixture for audit log
@pytest.fixture
def audit_log(tmp_path):
    """Set up an audit log file for traceability."""
    log_file = tmp_path / "audit.log"
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]'
    ))
    logger.addHandler(handler)
    yield log_file
    logger.removeHandler(handler)

# Fixture for mock RunnerConfig
@pytest.fixture
def mock_config(tmp_path):
    """Create a mock RunnerConfig for testing."""
    return RunnerConfig(
        version=4,
        backend='docker',
        framework='pytest',
        instance_id='test_instance',
        log_sinks=[
            {'type': 'file', 'config': {'path': str(tmp_path / 'test.log')}},
            {'type': 'datadog', 'config': {'api_key': SecretStr('mock_key')}},
            {'type': 'splunk', 'config': {'hec_url': 'http://mock.splunk', 'hec_token': SecretStr('mock_token')}},
            {'type': 'newrelic', 'config': {'api_key': SecretStr('mock_key')}}
        ],
        log_level='DEBUG',
        log_redaction_enabled=True,
        log_encryption_enabled=True,
        log_signing_enabled=True,
        log_signing_key='mock_signing_key',
        real_time_log_streaming=True
    )

# Helper function to log test execution for auditability
def log_test_execution(test_name, result, trace_id):
    """Log test execution details for audit trail."""
    logger.debug(
        f"Test {test_name}: {result}",
        extra={'trace_id': trace_id}
    )

# Test class for logging filters
class TestLoggingFilters:
    """Tests for logging filters in runner_logging.py."""

    @pytest.mark.asyncio
    async def test_redaction_filter_string_message(self, audit_log):
        """Test RedactionFilter on string messages."""
        trace_id = str(uuid.uuid4())
        filter = RedactionFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Sensitive: api_key=123xyz, email=test@example.com",
            args=(), exc_info=None
        )
        filter.filter(record)
        assert '[REDACTED]' in record.msg
        assert 'api_key=123xyz' not in record.msg
        assert 'test@example.com' not in record.msg
        log_test_execution("test_redaction_filter_string_message", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_redaction_filter_dict_message(self, audit_log):
        """Test RedactionFilter on dict messages."""
        trace_id = str(uuid.uuid4())
        filter = RedactionFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg={"sensitive": "api_key=123xyz", "email": "test@example.com"},
            args=(), exc_info=None
        )
        filter.filter(record)
        assert '[REDACTED]' in json.dumps(record.msg)
        log_test_execution("test_redaction_filter_dict_message", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_signing_filter_hmac(self, audit_log):
        """Test SigningFilter with HMAC."""
        trace_id = str(uuid.uuid4())
        filter = SigningFilter(signing_algo='hmac', signing_key='test_key')
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Test message", args=(), exc_info=None
        )
        filter.filter(record)
        assert 'signature' in record.__dict__
        assert 'hmac' in record.__dict__['signature']
        log_test_execution("test_signing_filter_hmac", "Passed", trace_id)

    @pytest.mark.asyncio
    @patch('runner.logging.HAS_ECDSA', True)
    @patch('runner.logging.ecdsa')
    async def test_signing_filter_ecdsa(self, mock_ecdsa, audit_log):
        """Test SigningFilter with ECDSA."""
        trace_id = str(uuid.uuid4())
        mock_key = MagicMock()
        mock_ecdsa.SigningKey.from_string.return_value = mock_key
        mock_key.sign.return_value = b'mock_signature'
        filter = SigningFilter(signing_algo='ecdsa', signing_key='test_key')
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Test message", args=(), exc_info=None
        )
        filter.filter(record)
        assert 'signature' in record.__dict__
        assert 'ecdsa' in record.__dict__['signature']
        log_test_execution("test_signing_filter_ecdsa", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_encryption_filter(self, audit_log):
        """Test EncryptionFilter."""
        trace_id = str(uuid.uuid4())
        with patch.dict(os.environ, {'FERNET_KEY': base64.urlsafe_b64encode(os.urandom(32)).decode()}):
            filter = EncryptionFilter()
            record = logging.LogRecord(
                name="test", level=logging.INFO, pathname="", lineno=0,
                msg="Test message", args=(), exc_info=None
            )
            filter.filter(record)
            assert 'encrypted_msg' in record.__dict__
            assert record.msg != "Test message"  # Encrypted
            log_test_execution("test_encryption_filter", "Passed", trace_id)

# Test class for logging formatters
class TestLoggingFormatters:
    """Tests for logging formatters in runner_logging.py."""

    @pytest.mark.asyncio
    async def test_structured_json_formatter(self, audit_log):
        """Test StructuredJSONFormatter."""
        trace_id = str(uuid.uuid4())
        formatter = StructuredJSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Test message", args=(), exc_info=None
        )
        record.__dict__['trace_id'] = 'mock_trace'
        formatted = formatter.format(record)
        parsed = json.loads(formatted)
        assert parsed['message'] == "Test message"
        assert 'timestamp_utc' in parsed
        assert parsed['level'] == "INFO"
        log_test_execution("test_structured_json_formatter", "Passed", trace_id)

# Test class for logging configuration and actions
class TestLoggingConfiguration:
    """Tests for logging configuration and action functions in runner_logging.py."""

    @pytest.mark.asyncio
    @patch('runner.logging.datadog')
    @patch('runner.logging.splunk_handler')
    @patch('runner.logging.newrelic_logging_handler')
    @patch('aiohttp.ClientSession.post')
    async def test_configure_logging_from_config(self, mock_post, mock_newrelic, mock_splunk, mock_datadog, mock_config, audit_log):
        """Test configure_logging_from_config function."""
        trace_id = str(uuid.uuid4())
        mock_datadog.initialize = MagicMock()
        mock_splunk.SplunkHandler = MagicMock()
        mock_newrelic.NewRelicHandler = MagicMock()
        mock_post.return_value.__aenter__.return_value = AsyncMock(status=200)
        configure_logging_from_config(mock_config)
        # Verify root logger has handlers
        assert len(logging.getLogger().handlers) > 0
        log_test_execution("test_configure_logging_from_config", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_log_action(self, audit_log):
        """Test log_action function."""
        trace_id = str(uuid.uuid4())
        log_action("TestAction", {"key": "value"}, run_id="mock_run", provenance_hash="mock_hash")
        assert len(LOG_HISTORY) > 0
        last_log = LOG_HISTORY[-1]
        assert last_log['action'] == "TestAction"
        assert last_log['data'] == {"key": "value"}
        log_test_execution("test_log_action", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_search_logs(self, audit_log):
        """Test search_logs function."""
        trace_id = str(uuid.uuid4())
        LOG_HISTORY.append({"message": "sensitive data", "run_id": "mock_run"})
        results = search_logs(query="sensitive", limit=1, run_id="mock_run")
        assert len(results) == 1
        assert "sensitive data" in results[0]['message']
        log_test_execution("test_search_logs", "Passed", trace_id)

# Integration test class
class TestLoggingIntegration:
    """Integration tests for logging workflows."""

    @pytest.mark.asyncio
    @patch('runner.logging.datadog')
    async def test_full_logging_pipeline(self, mock_datadog, mock_config, tmp_path, audit_log):
        """Test full logging pipeline with redaction, signing, and encryption."""
        trace_id = str(uuid.uuid4())
        mock_datadog.initialize = MagicMock()
        configure_logging_from_config(mock_config)
        test_logger = logging.getLogger("integration_test")
        test_logger.info("Sensitive: api_key=123xyz")
        # Check LOG_HISTORY for processed log
        assert len(LOG_HISTORY) > 0
        last_log = LOG_HISTORY[-1]
        assert '[REDACTED]' in json.dumps(last_log)
        assert 'signature' in last_log
        assert 'encrypted_msg' in last_log
        log_test_execution("test_full_logging_pipeline", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_logging_error_propagation(self, mock_config, audit_log):
        """Test error propagation in logging configuration."""
        trace_id = str(uuid.uuid4())
        mock_config.log_signing_algo = 'invalid_algo'
        with pytest.raises(ConfigurationError) as exc_info:
            configure_logging_from_config(mock_config)
        assert exc_info.value.error_code == error_codes["CONFIGURATION_ERROR"]
        log_test_execution("test_logging_error_propagation", "Passed", trace_id)

# Run tests with audit logging
if __name__ == "__main__":
    pytest.main(["-v", "--log-level=DEBUG"])
