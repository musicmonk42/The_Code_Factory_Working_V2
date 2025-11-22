import pytest
from unittest.mock import patch, mock_open, ANY
import os
import json
import logging
import threading
from knowledge_loader import (
    merge_dict,
    save_knowledge_atomic,
    load_knowledge,
    KnowledgeLoader,
)
from unittest.mock import AsyncMock


# Fixture for mock logger
@pytest.fixture
def mock_logger():
    with patch.object(logging.getLogger(__name__), "info") as mock_info, patch.object(
        logging.getLogger(__name__), "debug"
    ) as mock_debug, patch.object(
        logging.getLogger(__name__), "warning"
    ) as mock_warning, patch.object(
        logging.getLogger(__name__), "error"
    ) as mock_error:
        yield mock_info, mock_debug, mock_warning, mock_error


# Test merge_dict function
@pytest.mark.parametrize(
    "orig, new, expected",
    [
        ({}, {"a": 1}, {"a": 1}),
        ({"a": 1}, {"b": 2}, {"a": 1, "b": 2}),
        ({"a": {"x": 10}}, {"a": {"y": 20}}, {"a": {"x": 10, "y": 20}}),
        ({"a": [1, 2]}, {"a": [2, 3]}, {"a": [1, 2, 3]}),
        ({"a": 1}, {"a": 2}, {"a": 2}),
    ],
)
def test_merge_dict(orig, new, expected):
    merge_dict(orig, new)
    assert orig == expected


# Test save_knowledge_atomic success
@patch("os.makedirs")
@patch("tempfile.mkstemp", return_value=(3, "/tmp/temp.json"))
@patch("os.fdopen", new_callable=mock_open)
@patch("json.dump")
@patch("os.replace")
@patch("os.remove")
def test_save_knowledge_atomic_success(
    mock_remove,
    mock_replace,
    mock_dump,
    mock_fdopen,
    mock_mkstemp,
    mock_makedirs,
    mock_logger,
):
    info, _, _, _ = mock_logger
    data = {"test": "data"}
    save_knowledge_atomic("test.json", data)
    mock_makedirs.assert_called_once_with("", exist_ok=True)
    mock_mkstemp.assert_called_once_with(dir="", prefix=".tmp_sfe_", suffix=".json")
    mock_fdopen.assert_called_once_with(3, "w", encoding="utf-8")
    mock_dump.assert_called_once_with(data, ANY, indent=2)
    mock_replace.assert_called_once_with("/tmp/temp.json", "test.json")
    info.assert_called_with("Saved aggregated knowledge to test.json atomically.")
    mock_remove.assert_not_called()


# Test save_knowledge_atomic failure
@patch("tempfile.mkstemp", return_value=(3, "/tmp/temp.json"))
@patch("os.fdopen", side_effect=IOError("mock error"))
@patch("os.remove")
def test_save_knowledge_atomic_failure(
    mock_remove, mock_fdopen, mock_mkstemp, mock_logger
):
    _, _, _, error = mock_logger
    data = {"test": "data"}
    with pytest.raises(IOError, match="Failed to save knowledge file test.json"):
        save_knowledge_atomic("test.json", data)
    error.assert_called_with("ERROR saving knowledge to test.json: mock error")
    mock_remove.assert_called_once_with("/tmp/temp.json")


# Test load_knowledge success
@patch("builtins.open", new_callable=mock_open, read_data='{"test": "data"}')
@patch("json.load", return_value={"test": "data"})
def test_load_knowledge_success(mock_json_load, mock_open, mock_logger):
    result = load_knowledge("test.json")
    assert result == {"test": "data"}
    mock_open.assert_called_once_with("test.json", "r", encoding="utf-8")


# Test load_knowledge file not found
@patch("builtins.open", side_effect=FileNotFoundError("not found"))
def test_load_knowledge_not_found(mock_open, mock_logger):
    _, _, _, error = mock_logger
    result = load_knowledge("missing.json")
    assert result is None
    error.assert_called_with("Knowledge file missing.json not found. Returning None.")


# Test load_knowledge invalid json
@patch("builtins.open", new_callable=mock_open)
@patch("json.load", side_effect=json.JSONDecodeError("invalid", "doc", 0))
def test_load_knowledge_invalid_json(mock_json_load, mock_open, mock_logger):
    _, _, _, error = mock_logger
    result = load_knowledge("invalid.json")
    assert result is None
    error.assert_called_with(
        "Malformed JSON in knowledge file invalid.json: Expecting value: line 1 column 1 (char 0). Returning None."
    )


# Test KnowledgeLoader initialization
@patch("os.walk")
@patch(
    "knowledge_loader.load_knowledge",
    side_effect=lambda f: (
        {os.path.basename(f): {"data": "test"}} if "test" in f else None
    ),
)
def test_knowledge_loader_init(mock_load, mock_walk, mock_logger):
    mock_walk.return_value = [("", [], ["test1.json", "test2.json"])]
    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    assert loader.loaded_knowledge == {
        "test1": {"data": "test"},
        "test2": {"data": "test"},
    }
    assert mock_load.call_count == 2


# Test aggregate_knowledge
@patch("knowledge_loader.load_knowledge", return_value={"existing": "data"})
def test_aggregate_knowledge(mock_load):
    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    loader.loaded_knowledge = {"new": "data"}
    loader.aggregate_knowledge()
    assert loader.aggregated_knowledge == {"existing": "data", "new": "data"}


# Test save_current_knowledge
@patch("knowledge_loader.save_knowledge_atomic")
def test_save_current_knowledge(mock_save):
    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    loader.aggregated_knowledge = {"test": "data"}
    loader.save_current_knowledge()
    mock_save.assert_called_once_with("master.json", {"test": "data"})


# Test load_and_aggregate
@pytest.mark.asyncio
@patch("asyncio.gather", new_callable=AsyncMock)
@patch("knowledge_loader.load_knowledge", return_value={"data": "test"})
async def test_load_and_aggregate(mock_load, mock_gather):
    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    loader.file_paths = ["file1.json", "file2.json"]
    mock_gather.return_value = [{"data": "test"}, {"data": "test"}]
    await loader.load_and_aggregate()
    assert loader.loaded_knowledge == {
        "file1": {"data": "test"},
        "file2": {"data": "test"},
    }


# Test inject_to_arbiter success
def test_inject_to_arbiter_success(mock_logger):
    class MockArbiter:
        state = {"memory": {}}
        name = "test_arbiter"

    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    loader.loaded_knowledge = {"domain": {"key": "value"}}
    arbiter = MockArbiter()
    loader.inject_to_arbiter(arbiter)
    assert arbiter.state["memory"] == {"domain": {"key": "value"}}


# Test inject_to_arbiter invalid state
def test_inject_to_arbiter_invalid_state(mock_logger):
    class MockArbiter:
        state = "invalid"

    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    arbiter = MockArbiter()
    loader.inject_to_arbiter(arbiter)
    _, _, _, error = mock_logger
    error.assert_called_with(
        "Arbiter instance does not have a valid 'state' dictionary."
    )


# Test inject_to_arbiter type mismatch
def test_inject_to_arbiter_type_mismatch(mock_logger):
    class MockArbiter:
        state = {"memory": {"domain": [1, 2]}}
        name = "test"

    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    loader.loaded_knowledge = {"domain": {"key": "value"}}
    arbiter = MockArbiter()
    loader.inject_to_arbiter(arbiter)
    _, _, warning, _ = mock_logger
    warning.assert_called_with("  Overwrote domain 'domain' due to type mismatch.")
    assert arbiter.state["memory"]["domain"] == {"key": "value"}


# Test thread safety in inject_to_arbiter
def test_inject_to_arbiter_thread_safety():
    class MockArbiter:
        state = {"memory": {}}
        name = "test"

    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    loader.loaded_knowledge = {"domain": {"key": "value"}}
    arbiter = MockArbiter()

    def inject():
        loader.inject_to_arbiter(arbiter)

    threads = [threading.Thread(target=inject) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert arbiter.state["memory"] == {
        "domain": {"key": "value"}
    }  # Only merged once effectively


# Test load_and_aggregate with malformed files
@pytest.mark.asyncio
@patch("asyncio.gather", new_callable=AsyncMock)
@patch("knowledge_loader.load_knowledge", side_effect=[{"data": "good"}, None])
async def test_load_and_aggregate_malformed(mock_load, mock_gather, mock_logger):
    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    loader.file_paths = ["good.json", "bad.json"]
    mock_gather.return_value = [{"data": "good"}, None]
    await loader.load_and_aggregate()
    assert loader.loaded_knowledge == {"good": {"data": "good"}}
    _, _, warning, _ = mock_logger
    warning.assert_called_with("Skipping malformed or empty knowledge file: bad.json")


# Test aggregate_knowledge with master file error
@patch("knowledge_loader.load_knowledge", side_effect=Exception("master error"))
def test_aggregate_knowledge_master_error(mock_load, mock_logger):
    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    with pytest.raises(Exception, match="master error"):
        loader.aggregate_knowledge()
    _, _, _, error = mock_logger
    error.assert_called_with(
        "Failed to load master knowledge file master.json: master error"
    )


# Test save_current_knowledge empty
def test_save_current_knowledge_empty(mock_logger):
    loader = KnowledgeLoader(knowledge_dir="dir", master_file="master.json")
    loader.aggregated_knowledge = {}
    loader.save_current_knowledge()
    _, debug, _, _ = mock_logger
    debug.assert_called_with("No aggregated knowledge to save. Skipping.")


# Test discover_knowledge_files
@patch("os.walk")
def test_discover_knowledge_files(mock_walk):
    mock_walk.return_value = [("/dir", [], ["file1.json", "file2.txt", "file3.json"])]
    loader = KnowledgeLoader(knowledge_dir="/dir", master_file="master.json")
    loader.discover_knowledge_files()
    assert loader.file_paths == ["/dir/file1.json", "/dir/file3.json"]
