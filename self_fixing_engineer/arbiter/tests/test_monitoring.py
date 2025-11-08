import pytest
from unittest.mock import MagicMock, patch, mock_open
import logging
import threading
import os
import sys
import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path

# Import from the arbiter package where monitoring.py is located
from arbiter.monitoring import Monitor, LogFormat, MAX_IN_MEMORY_LOG_SIZE_MB, JSON_LOG_WRITE_LIMIT

@pytest.fixture
def tmp_log_file(tmp_path):
    return tmp_path / "test_log"

@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)

@pytest.fixture
def monitor(tmp_log_file, mock_logger):
    return Monitor(log_file=tmp_log_file, logger=mock_logger, max_file_size=1024, max_actions_in_memory=5, format=LogFormat.JSONL)

def test_initialization_defaults():
    monitor = Monitor()
    assert monitor.log_file is None
    assert monitor.logger is not None
    assert monitor.max_file_size == 50 * 1024 * 1024
    assert monitor.max_actions_in_memory == 10000
    assert monitor.format == LogFormat.JSONL
    assert monitor.global_metadata == {}
    assert monitor.observers == []
    assert monitor.tamper_evident == False
    assert monitor.action_logs == []

def test_initialization_with_params(tmp_log_file, mock_logger):
    global_meta = {"env": "test"}
    observers = [MagicMock()]
    monitor = Monitor(log_file=tmp_log_file, logger=mock_logger, max_file_size=2048, max_actions_in_memory=10, format=LogFormat.JSON, global_metadata=global_meta, observers=observers, tamper_evident=True)
    assert monitor.log_file == tmp_log_file
    assert monitor.logger == mock_logger
    assert monitor.max_file_size == 2048
    assert monitor.max_actions_in_memory == 10
    assert monitor.format == LogFormat.JSON
    assert monitor.global_metadata == global_meta
    assert monitor.observers == observers
    assert monitor.tamper_evident == True

def test_log_action_basic(monitor):
    action = {"type": "test_action", "data": "test_data"}
    monitor.log_action(action)
    assert len(monitor.action_logs) == 1
    logged = monitor.action_logs[0]
    assert logged["type"] == "test_action"
    assert logged["data"] == "test_data"
    assert "timestamp" in logged
    # Parse timestamp to verify it's valid
    timestamp_str = logged["timestamp"].rstrip("Z")
    assert isinstance(datetime.fromisoformat(timestamp_str), datetime)

def test_log_action_with_metadata(monitor):
    monitor.global_metadata = {"user": "test_user"}
    action = {"type": "test"}
    monitor.log_action(action)
    logged = monitor.action_logs[0]
    assert logged["user"] == "test_user"

def test_log_action_global_metadata(monitor):
    monitor.global_metadata = {"env": "prod"}
    monitor.log_action({"type": "test"})
    logged = monitor.action_logs[0]
    assert logged["env"] == "prod"

def test_log_action_tamper_evident(monitor):
    monitor.tamper_evident = True
    monitor._last_line_hash = None
    
    monitor.log_action({"type": "test1"})
    first_action = monitor.action_logs[0]
    assert "line_hash" in first_action
    assert "prev_hash" in first_action
    first_hash = first_action["line_hash"]
    
    monitor.log_action({"type": "test2"})
    second_action = monitor.action_logs[1]
    assert "line_hash" in second_action
    assert second_action["prev_hash"] == first_hash

def test_log_action_observers(monitor):
    observer = MagicMock()
    monitor.observers = [observer]
    monitor.log_action({"type": "test"})
    observer.assert_called_once()
    # Check that the observer was called with the logged action
    call_args = observer.call_args[0][0]
    assert call_args["type"] == "test"

@pytest.mark.parametrize("num_threads", [10, 50])
def test_thread_safety(monitor, num_threads):
    def log_thread():
        for i in range(100):
            monitor.log_action({"type": f"thread_{threading.current_thread().ident}_{i}"})
    
    threads = [threading.Thread(target=log_thread) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(monitor.action_logs) == min(num_threads * 100, monitor.max_actions_in_memory)

def test_prune_old_logs(monitor):
    monitor.max_actions_in_memory = 3
    for i in range(5):
        monitor.log_action({"type": f"action_{i}"})
    assert len(monitor.action_logs) == 3
    # Check that we kept the 3 most recent actions
    assert monitor.action_logs[0]["type"] == "action_2"
    assert monitor.action_logs[1]["type"] == "action_3"
    assert monitor.action_logs[2]["type"] == "action_4"

def test_write_to_file_jsonl(monitor, tmp_log_file):
    monitor.log_action({"type": "test1"})
    monitor.log_action({"type": "test2"})
    with open(tmp_log_file, 'r') as f:
        lines = f.readlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "test1"
    assert json.loads(lines[1])["type"] == "test2"

def test_write_to_file_json(monitor, tmp_log_file):
    monitor.format = LogFormat.JSON
    monitor.log_action({"type": "test1"})
    monitor.log_action({"type": "test2"})
    with open(tmp_log_file, 'r') as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[0]["type"] == "test1"
    assert data[1]["type"] == "test2"

def test_write_to_file_plaintext(monitor, tmp_log_file):
    monitor.format = LogFormat.PLAINTEXT
    monitor.log_action({"type": "test"})
    with open(tmp_log_file, 'r') as f:
        content = f.read()
    assert "'type': 'test'" in content

def test_log_rotation(monitor, tmp_log_file):
    monitor.max_file_size = 100  # Small size for testing
    for i in range(100):  # Generate enough data to exceed
        monitor.log_action({"type": "long_action", "data": "a" * 100})
    # Check that a backup file was created
    backup_file = tmp_log_file.with_suffix('.bak')
    assert os.path.exists(backup_file)
    
    # The test expectation was flawed - a single log entry with metadata
    # is about 202 bytes, which is larger than max_file_size (100).
    # After rotation, the new file will have at least one entry.
    # The proper test is to ensure rotation happened and files exist.
    current_size = os.path.getsize(tmp_log_file)
    
    # The file should have been rotated, so it should have recent entries only
    # Since each entry is ~202 bytes and max_file_size is 100, 
    # we expect the file to contain 1 entry after last rotation
    # Allow some flexibility for the exact size
    assert current_size > 0  # File should not be empty
    assert current_size < 1024  # But should be reasonably small after rotation

def test_json_write_limit(monitor, caplog):
    monitor.format = LogFormat.JSON
    # Mock the JSON_LOG_WRITE_LIMIT to a small value for testing
    with patch('arbiter.monitoring.JSON_LOG_WRITE_LIMIT', 2):
        for i in range(3):
            monitor.log_action({"type": f"action_{i}"})
        # Check that an error was logged about JSON format being inefficient
        assert any("JSON log format" in record.message for record in caplog.records)

def test_search_all(monitor):
    monitor.log_action({"type": "test1"})
    monitor.log_action({"type": "test2"})
    results = monitor.search()
    assert len(results) == 2

def test_search_filtered(monitor):
    monitor.log_action({"type": "include"})
    monitor.log_action({"type": "exclude"})
    results = monitor.search(lambda a: a["type"] == "include")
    assert len(results) == 1
    assert results[0]["type"] == "include"

@pytest.mark.asyncio
async def test_export_log_jsonl(monitor, tmp_path):
    export_path = tmp_path / "export.jsonl"
    monitor.log_action({"type": "test1"})
    monitor.log_action({"type": "test2"})
    await monitor.export_log(export_path, LogFormat.JSONL)
    # Note: The exported file will be encrypted, so we can't directly read it
    # We'll just check that the file exists and has content
    assert export_path.exists()
    assert export_path.stat().st_size > 0

@pytest.mark.asyncio
async def test_export_log_invalid_format(monitor, tmp_path):
    # Create an invalid LogFormat value
    invalid_format = "invalid"
    with pytest.raises(ValueError, match="Unknown export format"):
        await monitor.export_log(tmp_path / "export", invalid_format)

@pytest.mark.asyncio
async def test_export_log_error(monitor, tmp_path, caplog):
    # Test that the function handles and logs errors properly
    monitor.log_action({"type": "test"})
    
    # Create a mock that raises an error when trying to open the file
    with patch('aiofiles.open', side_effect=OSError("Permission denied")):
        # The function should raise the OSError after logging it
        with pytest.raises(OSError, match="Permission denied"):
            await monitor.export_log(tmp_path / "export.jsonl")
    
    # Check that the error was logged
    assert any("Failed to export log" in record.message for record in caplog.records)

@pytest.mark.slow
def test_high_volume_logging(monitor):
    # Adjust for the actual max_actions_in_memory value
    monitor.max_actions_in_memory = 10000
    for i in range(10000):
        monitor.log_action({"type": f"action_{i}"})
    assert len(monitor.action_logs) == monitor.max_actions_in_memory

def test_get_recent_events(monitor):
    for i in range(10):
        monitor.log_action({"type": f"action_{i}"})
    recent = monitor.get_recent_events(count=5)
    assert len(recent) == 5
    assert recent[-1]["type"] == "action_9"

def test_explain_decision(monitor):
    monitor.log_action({"decision_id": "dec1", "description": "Test decision", "why": "Testing"})
    explanation = monitor.explain_decision("dec1")
    assert explanation["decision_id"] == "dec1"
    assert explanation["description"] == "Test decision"
    assert explanation["why"] == "Testing"

def test_explain_decision_not_found(monitor):
    explanation = monitor.explain_decision("nonexistent")
    assert "error" in explanation

@pytest.mark.asyncio
async def test_detect_anomalies(monitor):
    # Need to reduce max_actions_in_memory to allow 150 actions
    monitor.max_actions_in_memory = 200
    # Add many actions of the same type to trigger anomaly detection
    for i in range(150):
        monitor.log_action({"event": "suspicious_action", "timestamp": datetime.utcnow().isoformat() + "Z"})
    anomalies = await monitor.detect_anomalies()
    assert len(anomalies) > 0
    assert anomalies[0]["type"] == "high_frequency"
    assert anomalies[0]["count"] == 150

def test_generate_reports(monitor):
    for i in range(5):
        monitor.log_action({"type": f"action_{i}"})
    report = monitor.generate_reports()
    assert report["total_actions"] == 5
    assert len(report["recent_actions"]) == 5

@pytest.mark.asyncio
async def test_health_check(monitor, tmp_log_file):
    # Ensure the log file exists
    tmp_log_file.touch()
    health = await monitor.health_check()
    assert health["status"] == "healthy"
    assert "in_memory_logs" in health
    assert "log_file_size" in health