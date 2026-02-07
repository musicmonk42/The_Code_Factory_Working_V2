# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import base64
import json
import logging
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Save original modules before mocking
_ORIGINAL_MODULES = {}
_MODULES_TO_MOCK = [
    "plugins.dlt_backend",
    "syslog",
]
for _mod in _MODULES_TO_MOCK:
    if _mod in sys.modules:
        _ORIGINAL_MODULES[_mod] = sys.modules[_mod]


# Create proper stub modules with __path__ attribute to avoid breaking pytest collection
def _create_stub_module(name):
    """Create a proper stub module that won't break import machinery."""
    stub = types.ModuleType(name)
    stub.__file__ = f"<stub {name}>"
    stub.__path__ = []
    stub.__spec__ = None
    return stub


# Mock non-opentelemetry third-party dependencies before importing
# NOTE: Do NOT mock opentelemetry - it's already installed and mocking it breaks other tests
# NOTE: Do NOT mock cryptography - other tests need the real module
if "plugins.dlt_backend" not in sys.modules:
    sys.modules["plugins.dlt_backend"] = _create_stub_module("plugins.dlt_backend")
if "syslog" not in sys.modules:
    sys.modules["syslog"] = _create_stub_module("syslog")

# Add self_fixing_engineer directory to the path (go up 3 levels from test file)
# Note: Don't add arbiter dir directly because arbiter.py would shadow the package
_sfe_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _sfe_dir not in sys.path:
    sys.path.insert(0, _sfe_dir)

# Import the module under test
from self_fixing_engineer.arbiter.audit_log import (
    AuditLoggerConfig,
    CompressionType,
    RotationType,
    SizedTimedRotatingFileHandler,
    TamperEvidentLogger,
    log_event,
    verify_log_integrity,
)


def _restore_original_modules():
    """Restore original modules that were patched during test import."""
    for mod_name in _MODULES_TO_MOCK:
        if mod_name in _ORIGINAL_MODULES:
            sys.modules[mod_name] = _ORIGINAL_MODULES[mod_name]
        elif mod_name in sys.modules:
            # Check if it's our stub module
            module = sys.modules[mod_name]
            if isinstance(module, types.ModuleType) and hasattr(module, '__file__') and '<stub' in str(module.__file__):
                del sys.modules[mod_name]


@pytest.fixture(scope="module", autouse=True)
def cleanup_mocked_modules():
    """Restore original modules when this test module finishes."""
    yield
    _restore_original_modules()


# Fixtures
@pytest.fixture
def temp_log_dir():
    """Create a temporary directory for test logs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def basic_config(temp_log_dir):
    """Create a basic configuration for testing."""
    return AuditLoggerConfig(
        log_path=temp_log_dir / "test_audit.jsonl",
        rotation_type=RotationType.MIDNIGHT,
        retention_count=5,
        batch_size=3,
        batch_timeout=0.1,
        encrypt_logs=False,
        async_logging=True,
        dlt_enabled=False,
        syslog_enabled=False,
        metrics_enabled=False,
    )


@pytest.fixture
def encrypted_config(temp_log_dir):
    """Create a configuration with encryption enabled."""
    return AuditLoggerConfig(
        log_path=temp_log_dir / "encrypted_audit.jsonl",
        encrypt_logs=True,
        encryption_key="test-encryption-key-123456789012",
        batch_size=2,
        batch_timeout=0.1,
    )


@pytest.fixture
async def logger_instance(basic_config):
    """Create a fresh logger instance for each test."""
    # Reset singleton and clear handlers to avoid PermissionError on Windows
    TamperEvidentLogger._instance = None
    logger_name = "AuditLogger"
    existing_logger = logging.getLogger(logger_name)
    for handler in existing_logger.handlers[:]:
        handler.close()
        existing_logger.removeHandler(handler)

    logger = TamperEvidentLogger(basic_config)
    yield logger

    # Teardown logic
    for handler in logger._logger.handlers[:]:
        handler.close()
        logger._logger.removeHandler(handler)
    if logger._batch_task and not logger._batch_task.done():
        logger._batch_task.cancel()
        try:
            await logger._batch_task
        except asyncio.CancelledError:
            pass


@pytest.fixture
async def encrypted_logger(encrypted_config):
    """Create a logger instance with encryption enabled."""
    # Reset singleton and clear handlers to avoid PermissionError on Windows
    TamperEvidentLogger._instance = None
    logger_name = "AuditLogger"
    existing_logger = logging.getLogger(logger_name)
    for handler in existing_logger.handlers[:]:
        handler.close()
        existing_logger.removeHandler(handler)

    # Mock Fernet
    mock_fernet = MagicMock()
    mock_fernet.encrypt.return_value = b"encrypted_data"
    mock_fernet.decrypt.return_value = b'{"test": "data"}'

    with patch("self_fixing_engineer.arbiter.audit_log.Fernet", return_value=mock_fernet):
        with patch("self_fixing_engineer.arbiter.audit_log.PBKDF2HMAC"):
            logger = TamperEvidentLogger(encrypted_config)
            logger._fernet = mock_fernet
            yield logger

    # Teardown logic
    for handler in logger._logger.handlers[:]:
        handler.close()
        logger._logger.removeHandler(handler)
    if logger._batch_task and not logger._batch_task.done():
        logger._batch_task.cancel()
        try:
            await logger._batch_task
        except asyncio.CancelledError:
            pass


# Test Configuration
class TestAuditLoggerConfig:
    def test_valid_config_initialization(self, temp_log_dir):
        """Test that valid configuration initializes correctly."""
        config = AuditLoggerConfig(
            log_path=temp_log_dir / "test.jsonl",
            rotation_type=RotationType.SIZE,
            max_file_size=1024,
            retention_count=10,
        )
        assert config.log_path == temp_log_dir / "test.jsonl"
        assert config.rotation_type == RotationType.SIZE
        assert config.max_file_size == 1024

    def test_invalid_rotation_type(self, temp_log_dir):
        """Test that invalid rotation type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid rotation_type"):
            AuditLoggerConfig(
                log_path=temp_log_dir / "test.jsonl", rotation_type="invalid_type"
            )

    def test_invalid_compression_type(self, temp_log_dir):
        """Test that invalid compression type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid compression_type"):
            AuditLoggerConfig(
                log_path=temp_log_dir / "test.jsonl",
                compression_type="invalid_compression",
            )

    def test_negative_retention_count(self, temp_log_dir):
        """Test that negative retention count raises ValueError."""
        with pytest.raises(ValueError, match="retention_count must be non-negative"):
            AuditLoggerConfig(log_path=temp_log_dir / "test.jsonl", retention_count=-1)

    def test_encryption_without_key_generates_key(self, temp_log_dir):
        """Test that enabling encryption without a key generates one."""
        with patch("self_fixing_engineer.arbiter.audit_log.Fernet"):
            config = AuditLoggerConfig(
                log_path=temp_log_dir / "test.jsonl", encrypt_logs=True
            )
            assert config.encryption_key is not None
            assert len(base64.urlsafe_b64decode(config.encryption_key)) == 32


# Test Logger Initialization
class TestLoggerInitialization:
    def test_singleton_pattern(self, basic_config):
        """Test that logger follows singleton pattern."""
        TamperEvidentLogger._instance = None
        logger_name = "AuditLogger"
        existing_logger = logging.getLogger(logger_name)
        existing_logger.handlers.clear()

        logger1 = TamperEvidentLogger(basic_config)
        logger2 = TamperEvidentLogger(basic_config)
        assert logger1 is logger2

        for handler in logger1._logger.handlers[:]:
            handler.close()
            logger1._logger.removeHandler(handler)

    def test_logger_creates_log_directory(self, temp_log_dir):
        """Test that logger creates log directory if it doesn't exist."""
        config = AuditLoggerConfig(log_path=temp_log_dir / "subdir" / "test.jsonl")
        TamperEvidentLogger._instance = None
        logger = TamperEvidentLogger(config)
        assert (temp_log_dir / "subdir").exists()
        for handler in logger._logger.handlers[:]:
            handler.close()
            logger._logger.removeHandler(handler)

    @patch("self_fixing_engineer.arbiter.audit_log.SizedTimedRotatingFileHandler")
    def test_file_handler_setup(self, mock_handler, basic_config):
        """Test that file handler is set up correctly."""
        TamperEvidentLogger._instance = None
        logger_name = "AuditLogger"
        existing_logger = logging.getLogger(logger_name)
        existing_logger.handlers.clear()

        logger = TamperEvidentLogger(basic_config)
        mock_handler.assert_called_once()
        for handler in logger._logger.handlers[:]:
            handler.close()
            logger._logger.removeHandler(handler)


# Test Basic Logging
class TestBasicLogging:
    @pytest.mark.asyncio
    async def test_log_single_event(self, logger_instance):
        """Test logging a single event."""
        details = {"action": "test_action", "value": 42}
        hash_val = await logger_instance.log_event(
            "test_event", details, user_id="user123"
        )

        assert hash_val is not None
        assert len(hash_val) == 64  # SHA256 hash length
        assert logger_instance._last_hash == hash_val

    @pytest.mark.asyncio
    async def test_log_multiple_events(self, logger_instance):
        """Test logging multiple events maintains hash chain."""
        hashes = []
        for i in range(3):
            details = {"index": i}
            hash_val = await logger_instance.log_event(f"event_{i}", details)
            hashes.append(hash_val)

        assert len(set(hashes)) == 3  # All hashes should be unique
        assert logger_instance._last_hash == hashes[-1]

    @pytest.mark.asyncio
    async def test_critical_event_triggers_immediate_flush(self, logger_instance):
        """Test that critical events trigger immediate batch flush."""
        logger_instance._log_to_file_async = AsyncMock()

        await logger_instance.log_event(
            "critical_event", {"data": "important"}, critical=True
        )

        # Critical event should trigger immediate flush
        logger_instance._log_to_file_async.assert_called()

    @pytest.mark.asyncio
    async def test_batch_processing(self, logger_instance):
        """Test that events are batched correctly."""
        logger_instance.config.batch_size = 3
        logger_instance._log_to_file_async = AsyncMock()

        # Add events to fill batch
        for i in range(3):
            await logger_instance.log_event(f"event_{i}", {"index": i})

        # Batch should be flushed after reaching batch_size
        await asyncio.sleep(0.01)  # Allow async operations to complete
        logger_instance._log_to_file_async.assert_called()

    @pytest.mark.asyncio
    async def test_invalid_event_type(self, logger_instance):
        """Test that invalid event types raise ValueError when configured."""
        logger_instance.config.valid_event_types = ["allowed_type"]

        with pytest.raises(ValueError, match="Invalid event_type"):
            await logger_instance.log_event("forbidden_type", {})


# Test Encryption
class TestEncryption:
    @pytest.mark.asyncio
    async def test_encrypt_sensitive_fields(self, encrypted_logger):
        """Test that sensitive fields are encrypted."""
        entry = {
            "event": "test",
            "details": {"sensitive": "data"},
            "extra": {"more": "sensitive"},
        }

        encrypted = encrypted_logger._encrypt_entry(entry)

        # Check that sensitive fields were encrypted
        assert encrypted["details"] == "encrypted_data"
        assert encrypted["extra"] == "encrypted_data"
        encrypted_logger._fernet.encrypt.assert_called()

    @pytest.mark.asyncio
    async def test_decrypt_sensitive_fields(self, encrypted_logger):
        """Test that sensitive fields are decrypted correctly."""
        encrypted_entry = {
            "event": "test",
            "details": "encrypted_data",
            "extra": "encrypted_data",
        }

        decrypted = encrypted_logger._decrypt_entry(encrypted_entry)

        # Check that fields were decrypted
        assert decrypted["details"] == {"test": "data"}
        assert decrypted["extra"] == {"test": "data"}


# Test Hash Chain Integrity
class TestHashChainIntegrity:
    def test_hash_calculation(self):
        """Test that hash calculation is deterministic."""
        entry = {"event": "test", "data": "value"}
        hash1 = TamperEvidentLogger._hash_entry("prev_hash", entry)
        hash2 = TamperEvidentLogger._hash_entry("prev_hash", entry)
        assert hash1 == hash2

    def test_hash_chain_different_with_different_data(self):
        """Test that different data produces different hashes."""
        entry1 = {"event": "test1", "data": "value1"}
        entry2 = {"event": "test2", "data": "value2"}
        hash1 = TamperEvidentLogger._hash_entry("prev", entry1)
        hash2 = TamperEvidentLogger._hash_entry("prev", entry2)
        assert hash1 != hash2

    @pytest.mark.asyncio
    async def test_verify_log_integrity_valid(self, logger_instance, temp_log_dir):
        """Test integrity verification on valid log."""
        # Write some valid log entries
        entries = []
        prev_hash = None
        for i in range(3):
            entry = {
                "event_type": f"event_{i}",
                "details": {"index": i},
                "timestamp": datetime.now().isoformat(),
                "previous_hash": prev_hash,
            }
            entry["current_hash"] = TamperEvidentLogger._hash_entry(prev_hash, entry)
            prev_hash = entry["current_hash"]
            entries.append(entry)

        # Write entries to file
        log_file = temp_log_dir / "integrity_test.jsonl"
        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        # Verify integrity
        is_valid, line_num, file_path = await logger_instance.verify_log_integrity(
            log_file
        )
        assert is_valid is True
        assert line_num is None
        assert file_path is None

    @pytest.mark.asyncio
    async def test_verify_log_integrity_tampered(self, logger_instance, temp_log_dir):
        """Test integrity verification detects tampering."""
        # Create valid entries but tamper with one
        entries = []
        prev_hash = None
        for i in range(3):
            entry = {
                "event_type": f"event_{i}",
                "details": {"index": i},
                "timestamp": datetime.now().isoformat(),
                "previous_hash": prev_hash,
            }
            entry["current_hash"] = TamperEvidentLogger._hash_entry(prev_hash, entry)
            prev_hash = entry["current_hash"]
            entries.append(entry)

        # Tamper with middle entry
        entries[1]["details"]["index"] = 999

        # Write tampered entries to file
        log_file = temp_log_dir / "tampered_test.jsonl"
        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        # Verify integrity should fail
        is_valid, line_num, file_path = await logger_instance.verify_log_integrity(
            log_file
        )
        assert is_valid is False
        assert line_num == 2  # Second line is tampered
        assert str(log_file) in str(file_path)


# Test Data Sanitization
class TestDataSanitization:
    def test_sanitize_dict_normal(self):
        """Test normal dictionary sanitization."""
        data = {"key": "value", "nested": {"inner": "data"}}
        result = TamperEvidentLogger._sanitize_dict(data, 1024)
        assert result == data

    def test_sanitize_dict_truncates_large_strings(self):
        """Test that large strings are truncated."""
        large_string = "x" * 1000
        data = {"large": large_string}
        result = TamperEvidentLogger._sanitize_dict(data, 100)
        assert "truncated" in result["large"]

    def test_sanitize_dict_raises_on_oversized(self):
        """Test that oversized dictionaries raise ValueError."""
        data = {"key": "x" * 1000}
        with pytest.raises(ValueError, match="exceeds"):
            TamperEvidentLogger._sanitize_dict(data, 10)


# Test Audit Trail Loading
class TestAuditTrailLoading:
    @pytest.mark.asyncio
    async def test_load_audit_trail_basic(self, logger_instance, temp_log_dir):
        """Test loading audit trail from file."""
        # Create test log file
        log_file = temp_log_dir / "trail_test.jsonl"
        entries = [
            {
                "event_type": "event1",
                "timestamp": "2024-01-01T10:00:00",
                "user_id": "user1",
            },
            {
                "event_type": "event2",
                "timestamp": "2024-01-01T11:00:00",
                "user_id": "user2",
            },
            {
                "event_type": "event3",
                "timestamp": "2024-01-01T12:00:00",
                "user_id": "user1",
            },
        ]

        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        # Load all entries
        loaded = list(logger_instance.load_audit_trail(log_file))
        assert len(loaded) == 3

    @pytest.mark.asyncio
    async def test_load_audit_trail_with_event_filter(
        self, logger_instance, temp_log_dir
    ):
        """Test loading audit trail with event type filter."""
        log_file = temp_log_dir / "filter_test.jsonl"
        entries = [
            {"event_type": "login", "user_id": "user1"},
            {"event_type": "logout", "user_id": "user1"},
            {"event_type": "login", "user_id": "user2"},
        ]

        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        # Load only login events
        loaded = list(logger_instance.load_audit_trail(log_file, event_type="login"))
        assert len(loaded) == 2
        assert all(e["event_type"] == "login" for e in loaded)

    @pytest.mark.asyncio
    async def test_load_audit_trail_with_user_filter(
        self, logger_instance, temp_log_dir
    ):
        """Test loading audit trail with user ID filter."""
        log_file = temp_log_dir / "user_filter_test.jsonl"
        entries = [
            {"event_type": "action", "user_id": "user1"},
            {"event_type": "action", "user_id": "user2"},
            {"event_type": "action", "user_id": "user1"},
        ]

        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        # Load only user1 entries
        loaded = list(logger_instance.load_audit_trail(log_file, user_id="user1"))
        assert len(loaded) == 2
        assert all(e["user_id"] == "user1" for e in loaded)

    @pytest.mark.asyncio
    async def test_load_audit_trail_with_time_filter(
        self, logger_instance, temp_log_dir
    ):
        """Test loading audit trail with time range filter."""
        log_file = temp_log_dir / "time_filter_test.jsonl"
        base_time = datetime(2024, 1, 1, 10, 0, 0)
        entries = [
            {"event_type": "event1", "timestamp": base_time.isoformat()},
            {
                "event_type": "event2",
                "timestamp": (base_time + timedelta(hours=1)).isoformat(),
            },
            {
                "event_type": "event3",
                "timestamp": (base_time + timedelta(hours=2)).isoformat(),
            },
        ]

        with open(log_file, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")

        # Load entries within time range
        start = base_time + timedelta(minutes=30)
        end = base_time + timedelta(hours=1, minutes=30)
        loaded = list(
            logger_instance.load_audit_trail(log_file, start_time=start, end_time=end)
        )
        assert len(loaded) == 1
        assert loaded[0]["event_type"] == "event2"


# Test DLT Integration
class TestDLTIntegration:
    @pytest.mark.asyncio
    async def test_dlt_anchoring_critical_events(self, logger_instance):
        """Test that critical events trigger DLT anchoring when enabled."""
        mock_dlt_client = MagicMock()
        # log_event_batch is called via run_in_executor (synchronously), not awaited
        mock_dlt_client.log_event_batch = MagicMock(return_value=["tx_123"])

        logger_instance.config.dlt_enabled = True
        logger_instance._dlt_client = mock_dlt_client

        await logger_instance.log_event(
            "critical", {"data": "important"}, critical=True
        )
        await asyncio.sleep(0.1)

        mock_dlt_client.log_event_batch.assert_called()

    @pytest.mark.asyncio
    async def test_dlt_retry_on_failure(self, logger_instance):
        """Test DLT retry mechanism on failure."""
        mock_dlt_client = MagicMock()
        mock_dlt_client.log_event_batch = MagicMock(
            side_effect=[Exception("Network error"), ["tx_456"]]
        )

        logger_instance.config.dlt_enabled = True
        logger_instance.config.dlt_retry_count = 1
        logger_instance._dlt_client = mock_dlt_client

        # Correct format with all required fields
        result = await logger_instance._anchor_to_dlt(
            [
                {
                    "event_type": "test",
                    "details": {},
                    "current_hash": "hash123",
                    "timestamp": "2024-01-01T00:00:00",
                    "agent": {},
                    "user_id": None,
                    "extra": None,
                }
            ]
        )

        assert mock_dlt_client.log_event_batch.call_count == 2
        assert result[0] == "tx_456"


# Test Rotation and Compression
class TestRotationAndCompression:
    def test_sized_rotating_handler_size_check(self, temp_log_dir):
        """Test that size-based rotation works."""
        log_file = temp_log_dir / "size_test.log"
        handler = SizedTimedRotatingFileHandler(
            str(log_file),
            when="midnight",
            interval=1,
            backupCount=3,
            maxBytes=100,
            compression_type=CompressionType.NONE,
        )

        # Create a mock record that would exceed size
        record = MagicMock()
        record.getMessage.return_value = "x" * 200

        # Mock the stream
        handler.stream = MagicMock()
        handler.stream.fileno.return_value = 1

        with patch("os.fstat") as mock_fstat:
            mock_fstat.return_value.st_size = 150
            assert handler.shouldRollover(record) is True

    @patch("gzip.open")
    @patch("pathlib.Path.open")
    @patch("pathlib.Path.exists")
    @patch("os.remove")
    def test_compression_on_rotation(
        self, mock_remove, mock_exists, mock_open, mock_gzip, temp_log_dir
    ):
        """Test that files are compressed on rotation."""
        log_file = temp_log_dir / "compress_test.log"
        handler = SizedTimedRotatingFileHandler(
            str(log_file),
            when="midnight",
            interval=1,
            backupCount=3,
            maxBytes=100,
            compression_type=CompressionType.GZIP,
        )

        mock_exists.return_value = True
        handler.baseFilename = str(log_file)

        # Trigger compression
        handler._compress_rotated_file()

        mock_gzip.assert_called_once()
        mock_remove.assert_called_once()


# Test Metrics
class TestMetrics:
    @pytest.mark.asyncio
    async def test_metrics_collection(self, logger_instance):
        """Test that metrics are collected when enabled."""
        mock_counter = MagicMock()
        mock_gauge = MagicMock()
        mock_histogram = MagicMock()

        logger_instance.config.metrics_enabled = True
        logger_instance._metrics = {
            "log_events_total": mock_counter,
            "batch_size": mock_gauge,
            "log_latency_seconds": mock_histogram,
        }

        await logger_instance.log_event("test", {"data": "value"})

        mock_counter.labels.assert_called_with(event_type="test")
        mock_gauge.set.assert_called()
        mock_histogram.observe.assert_called()


# Test Error Handling
class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_alert_callback_on_error(self, logger_instance):
        """Test that alert callback is called on errors."""
        alert_messages = []
        logger_instance.config.alert_callback = lambda msg: alert_messages.append(msg)
        logger_instance.config.max_details_size = 10

        # Try to log oversized details
        with pytest.raises(ValueError):
            await logger_instance.log_event("test", {"data": "x" * 1000})

        assert len(alert_messages) > 0
        assert "size limit" in alert_messages[0]

    @pytest.mark.asyncio
    async def test_malformed_json_handling(self, logger_instance, temp_log_dir):
        """Test handling of malformed JSON in log files."""
        log_file = temp_log_dir / "malformed.jsonl"
        with open(log_file, "w") as f:
            f.write('{"valid": "entry"}\n')
            f.write("not valid json\n")
            f.write('{"another": "valid"}\n')

        # Should skip malformed entry
        loaded = list(logger_instance.load_audit_trail(log_file))
        assert len(loaded) == 2


# Test Async Operations
class TestAsyncOperations:
    @pytest.mark.asyncio
    async def test_concurrent_logging(self, logger_instance):
        """Test that concurrent logging maintains consistency."""

        async def log_task(task_id):
            for i in range(5):
                await logger_instance.log_event(f"task_{task_id}", {"index": i})
                await asyncio.sleep(0.01)

        # Run multiple logging tasks concurrently
        tasks = [log_task(i) for i in range(3)]
        await asyncio.gather(*tasks)

        # All events should be logged
        assert (
            len(logger_instance._batch_queue) > 0
            or logger_instance._last_hash is not None
        )

    @pytest.mark.asyncio
    async def test_batch_timeout_processing(self, logger_instance):
        """Test that batch timeout triggers processing."""
        logger_instance.config.batch_timeout = 0.05
        logger_instance._log_to_file_async = AsyncMock()

        # Add single event (less than batch size)
        await logger_instance.log_event("test", {"data": "value"})

        # Wait for timeout
        await asyncio.sleep(0.1)

        # Batch should be processed due to timeout
        logger_instance._log_to_file_async.assert_called()


# Test Global API Functions
class TestGlobalAPI:
    @pytest.mark.asyncio
    async def test_global_log_event(self, basic_config):
        """Test global log_event function."""
        TamperEvidentLogger._instance = None
        with patch.object(
            TamperEvidentLogger, "log_event", new_callable=AsyncMock
        ) as mock_log:
            mock_log.return_value = "hash123"

            result = await log_event("test", {"data": "value"})

            assert result == "hash123"
            mock_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_global_verify_integrity(self, basic_config):
        """Test global verify_log_integrity function."""
        TamperEvidentLogger._instance = None
        with patch.object(
            TamperEvidentLogger, "verify_log_integrity", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = (True, None, None)

            result = await verify_log_integrity()

            assert result == (True, None, None)
            mock_verify.assert_called_once()


# Integration Tests
class TestIntegration:
    @pytest.mark.asyncio
    async def test_end_to_end_logging_and_verification(self, temp_log_dir):
        """Test complete flow: log events, then verify integrity."""
        config = AuditLoggerConfig(
            log_path=temp_log_dir / "e2e_test.jsonl",
            batch_size=2,
            batch_timeout=0.1,
            async_logging=False,  # Use sync for predictable file writes
        )

        TamperEvidentLogger._instance = None
        logger = TamperEvidentLogger(config)

        # Log several events
        events = [
            ("login", {"user": "alice"}),
            ("action", {"type": "create", "resource": "document"}),
            ("logout", {"user": "alice"}),
        ]

        for event_type, details in events:
            await logger.log_event(event_type, details)

        # Wait for batch processing
        await asyncio.sleep(0.2)

        # Verify integrity
        is_valid, _, _ = await logger.verify_log_integrity()
        assert is_valid

        # Load and verify audit trail
        trail = list(logger.load_audit_trail(event_type="login"))
        assert len(trail) == 1
        assert trail[0]["details"]["user"] == "alice"

        # Manually close handler for this logger to prevent PermissionError
        for handler in logger._logger.handlers[:]:
            handler.close()
            logger._logger.removeHandler(handler)

    @pytest.mark.asyncio
    async def test_rotation_and_compression_integration(self, temp_log_dir):
        """Test that rotation and compression work together."""
        config = AuditLoggerConfig(
            log_path=temp_log_dir / "rotation_test.jsonl",
            rotation_type=RotationType.SIZE,
            max_file_size=100,
            compression_type=CompressionType.GZIP,
            batch_size=1,
            async_logging=False,
        )

        TamperEvidentLogger._instance = None
        logger = TamperEvidentLogger(config)

        # Log enough to trigger rotation
        long_string = "x" * 150
        for i in range(3):
            await logger.log_event("event", {"data": long_string, "index": i})

        # Manually close the handler to force rollover and compression
        for handler in logger._logger.handlers[:]:
            handler.close()
            logger._logger.removeHandler(handler)

        # Check for rotated files
        log_files = list(temp_log_dir.glob("rotation_test.jsonl*"))
        assert len(log_files) > 1  # Should have original and rotated files
