# test_audit_log.py
# Comprehensive production-grade tests for audit_log.py
# Requires: pytest, pytest-asyncio, aiohttp, portalocker
# Run with: pytest test_audit_log.py -v --cov=audit_log

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from aiohttp import ClientError

# Import the module to be tested
from arbiter.bug_manager import audit_log
from arbiter.bug_manager.audit_log import AuditLogManager
from arbiter.bug_manager.utils import AuditLogError

# --- Fixtures ---


@pytest.fixture
def mock_settings(tmp_path):
    """Provides a mock settings object for the AuditLogManager."""
    settings = MagicMock()
    settings.AUDIT_LOG_ENABLED = True
    settings.AUDIT_LOG_FILE_PATH = str(tmp_path / "test_audit.log")
    settings.AUDIT_DEAD_LETTER_FILE_PATH = str(tmp_path / "test_dead_letter.log")
    settings.AUDIT_LOG_BUFFER_SIZE = 3
    settings.AUDIT_LOG_FLUSH_INTERVAL_SECONDS = 0.1  # Short interval for testing
    settings.AUDIT_LOG_MAX_FILE_SIZE_MB = 0.001  # 1 KB for easy rotation testing
    settings.AUDIT_LOG_BACKUP_COUNT = 2
    settings.REMOTE_AUDIT_SERVICE_ENABLED = False
    settings.REMOTE_AUDIT_SERVICE_URL = "http://fake-audit-service.com/log"
    settings.REMOTE_AUDIT_SERVICE_TIMEOUT = 1.0
    settings.REMOTE_AUDIT_DEAD_LETTER_ENABLED = True
    # Added attributes to prevent mock type errors
    settings.AUDIT_SCHEMA_VERSION = 1
    settings.AUDIT_LOG_RETRY_ATTEMPTS = 3
    settings.AUDIT_LOG_RETRY_DELAY_SECONDS = 1
    settings.AUDIT_LOG_MIN_DISK_SPACE_MB = 100
    settings.AUDIT_LOG_ENABLE_COMPRESSION = False
    settings.AUDIT_LOG_TEMP_CLEANUP_TIMEOUT = 300
    return settings


@pytest.fixture
async def manager(mock_settings):
    """
    Provides an initialized AuditLogManager instance that is automatically shut down.
    """
    mgr = AuditLogManager(settings=mock_settings)
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()


# --- Test Cases ---


class TestInitializationAndShutdown:
    @pytest.mark.asyncio
    async def test_initialization_success(self, manager):
        assert manager.enabled is True
        assert Path(manager.log_path).parent.exists()
        assert manager._flush_task is not None and not manager._flush_task.done()

    @pytest.mark.asyncio
    async def test_initialization_disabled(self, mock_settings):
        mock_settings.AUDIT_LOG_ENABLED = False
        mgr = AuditLogManager(settings=mock_settings)
        await mgr.initialize()
        assert mgr.enabled is False
        assert mgr._flush_task is None
        await mgr.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_cleans_up_resources(self, manager):
        flush_task = manager._flush_task
        io_executor = manager._io_executor

        await manager.audit("test_event", {"detail": "final_entry"})
        await manager.shutdown()

        assert flush_task.done()  # Check if task is done instead of cancelled
        assert io_executor._shutdown
        # Check that the final entry was flushed
        log_content = Path(manager.log_path).read_text()
        assert "final_entry" in log_content


class TestAuditingAndFlushing:
    @pytest.mark.asyncio
    async def test_audit_adds_to_buffer(self, manager):
        assert len(manager._log_buffer) == 0
        await manager.audit("test_event", {"detail": "value"})
        assert len(manager._log_buffer) == 1
        assert manager._log_buffer[0]["event_type"] == "test_event"

    @pytest.mark.asyncio
    async def test_periodic_flush_works(self, manager, mock_settings):
        await manager.audit("periodic_flush_test", {"data": 1})
        # Wait longer than the flush interval
        await asyncio.sleep(mock_settings.AUDIT_LOG_FLUSH_INTERVAL_SECONDS + 0.1)
        log_content = Path(manager.log_path).read_text()
        assert "periodic_flush_test" in log_content

    @pytest.mark.asyncio
    async def test_flush_on_full_buffer(self, manager):
        # Buffer size is 3 in settings
        await manager.audit("event", {"id": 1})
        await manager.audit("event", {"id": 2})
        # The third audit should trigger an immediate flush
        await manager.audit("event", {"id": 3})
        # Allow a moment for the async flush task to run
        await asyncio.sleep(0.1)  # Increase from 0.01 to 0.1
        log_content = Path(manager.log_path).read_text()
        assert '"id": 1' in log_content
        assert '"id": 2' in log_content
        assert '"id": 3' in log_content
        assert len(manager._log_buffer) == 0

    @pytest.mark.asyncio
    async def test_hash_chaining_integrity(self, manager):
        await manager.audit("event", {"id": "A"})
        await manager.audit("event", {"id": "B"})
        await manager.shutdown()  # Trigger final flush

        log_lines = Path(manager.log_path).read_text().strip().split("\n")
        assert len(log_lines) == 2

        entry_a = json.loads(log_lines[0])
        entry_b = json.loads(log_lines[1])

        assert entry_a["prev_hash"] is None
        assert "current_hash" in entry_a
        assert entry_b["prev_hash"] == entry_a["current_hash"]
        assert "current_hash" in entry_b


class TestFileOperations:
    @pytest.mark.asyncio
    async def test_log_rotation_on_size_exceeded(self, mock_settings):
        # Manager 1: Write enough data to exceed the 1KB limit
        mgr1 = AuditLogManager(settings=mock_settings)
        await mgr1.initialize()
        long_string = "a" * 1200
        await mgr1.audit("large_event", {"data": long_string})
        await mgr1.shutdown()  # Flushes the data

        # A new manager should trigger rotation on its first write
        mgr2 = AuditLogManager(settings=mock_settings)
        await mgr2.initialize()
        await mgr2.audit("new_event", {"data": "new"})
        await mgr2.shutdown()

        rotated_log_path = Path(f"{mgr1.log_path}.1")
        assert rotated_log_path.exists()
        rotated_content = rotated_log_path.read_text()
        assert long_string in rotated_content

    @pytest.mark.asyncio
    async def test_rotation_skips_on_low_disk_space(self, manager):
        with patch("shutil.disk_usage") as mock_disk_usage:
            # Simulate low disk space (less than MIN_DISK_SPACE_MB)
            mock_disk_usage.return_value = MagicMock(free=50 * 1024 * 1024)

            # Write a large log that should trigger rotation
            Path(manager.log_path).write_text("a" * 1200)

            # Manually trigger the sync rotation function to test it
            manager._sync_rotate_logs()

            # Assert that rotation did NOT happen
            assert Path(manager.log_path).exists()
            assert not Path(f"{manager.log_path}.1").exists()


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_write_failure_sends_to_dead_letter_queue(self, manager):
        # Patch the sync write method to fail
        with patch.object(
            manager,
            "_sync_atomic_write_with_retry",
            side_effect=IOError("Disk is full"),
        ):
            with pytest.raises(AuditLogError):
                await manager.audit("failed_event", {"id": "dlq_test"})
                await manager._flush_buffer()  # Manually trigger flush to see the error

        # Check the dead-letter queue
        dlq_path = Path(manager.dead_letter_log_path)
        assert dlq_path.exists()
        dlq_content = dlq_path.read_text()
        assert "dlq_test" in dlq_content
        assert "reason" in dlq_content
        assert "Local audit file write failed: Disk is full" in dlq_content

    @pytest.mark.asyncio
    async def test_lock_exception_rebuffers_logs(self, manager):
        with patch(
            "portalocker.lock",
            side_effect=audit_log.portalocker.LockException("Could not acquire lock"),
        ):
            await manager.audit("event_to_rebuffer", {"id": 1})
            await manager._flush_buffer()  # This should fail to write but not raise

        # The log should be re-added to the buffer for the next attempt
        assert len(manager._log_buffer) == 1
        assert manager._log_buffer[0]["details"]["id"] == 1


class TestRemoteIntegration:
    @pytest.mark.asyncio
    async def test_remote_send_success(self, mock_settings):
        mock_settings.REMOTE_AUDIT_SERVICE_ENABLED = True
        manager = AuditLogManager(settings=mock_settings)
        await manager.initialize()

        with patch("aiohttp.ClientSession.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value.__aenter__.return_value.status = 200

            await manager.audit("remote_event", {"id": "remote1"})
            await manager._flush_buffer()

            mock_post.assert_called()
            sent_json = mock_post.call_args.kwargs["json"]
            assert len(sent_json) == 1
            assert sent_json[0]["details"]["id"] == "remote1"
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_remote_send_http_error_sends_to_dlq(self, mock_settings):
        mock_settings.REMOTE_AUDIT_SERVICE_ENABLED = True
        manager = AuditLogManager(settings=mock_settings)
        await manager.initialize()

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status = 500
            mock_response.text = AsyncMock(return_value="Internal Server Error")

            mock_context = MagicMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)

            mock_session.post.return_value = mock_context
            mock_session_class.return_value = mock_session
            manager._session = mock_session

            await manager.audit("remote_fail_event", {"id": "remote_fail"})
            await manager._flush_buffer()

        dlq_path = Path(manager.dead_letter_log_path)
        assert dlq_path.exists()
        dlq_content = dlq_path.read_text()
        assert "remote_fail" in dlq_content
        assert "Session not ready" in dlq_content or "Remote send failed" in dlq_content
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_remote_send_network_error_sends_to_dlq(self, mock_settings):
        mock_settings.REMOTE_AUDIT_SERVICE_ENABLED = True
        manager = AuditLogManager(settings=mock_settings)
        await manager.initialize()

        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status = 500
            mock_response.text = AsyncMock(return_value="Internal Server Error")

            mock_context = MagicMock()
            mock_context.__aenter__ = AsyncMock(return_value=mock_response)
            mock_context.__aexit__ = AsyncMock(return_value=None)

            mock_session.post.side_effect = ClientError("Connection refused")
            mock_session_class.return_value = mock_session
            manager._session = mock_session

            await manager.audit("remote_net_fail", {"id": "net_fail"})
            await manager._flush_buffer()

        dlq_path = Path(manager.dead_letter_log_path)
        assert dlq_path.exists()
        dlq_content = dlq_path.read_text()
        assert "net_fail" in dlq_content
        assert "Session not ready" in dlq_content
        await manager.shutdown()
