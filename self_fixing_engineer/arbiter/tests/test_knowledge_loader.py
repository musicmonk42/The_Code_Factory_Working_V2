"""
Test suite for knowledge_loader module.
Tests are aligned with the actual implementation in arbiter/knowledge_loader.py
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import threading
from unittest.mock import ANY, AsyncMock, MagicMock, mock_open, patch

import pytest
from arbiter.knowledge_loader import (
    KnowledgeLoader,
    load_knowledge,
    merge_dict,
    save_knowledge_atomic,
)


# Fixture for temporary directory
@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    path = tempfile.mkdtemp()
    yield path
    shutil.rmtree(path, ignore_errors=True)


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
def test_save_knowledge_atomic_success(temp_dir):
    """Test that atomic save works correctly."""
    filepath = os.path.join(temp_dir, "test.json")
    data = {"test": "data"}
    save_knowledge_atomic(filepath, data)

    # Verify file was saved correctly
    with open(filepath, "r") as f:
        loaded = json.load(f)
    assert loaded == data


# Test save_knowledge_atomic failure - with non-existent directory
def test_save_knowledge_atomic_creates_directory(temp_dir):
    """Test that atomic save creates necessary directories."""
    subdir = os.path.join(temp_dir, "subdir")
    filepath = os.path.join(subdir, "test.json")
    data = {"test": "data"}
    save_knowledge_atomic(filepath, data)

    assert os.path.exists(filepath)
    with open(filepath, "r") as f:
        loaded = json.load(f)
    assert loaded == data


# Test _load_knowledge_sync success
def test_load_knowledge_sync_success(temp_dir):
    """Test synchronous knowledge loading."""
    filepath = os.path.join(temp_dir, "test.json")
    data = {"test": "data"}
    with open(filepath, "w") as f:
        json.dump(data, f)

    result = _load_knowledge_sync(filepath)
    assert result == data


# Test _load_knowledge_sync file not found
def test_load_knowledge_sync_not_found(temp_dir):
    """Test loading a non-existent file returns None."""
    filepath = os.path.join(temp_dir, "missing.json")
    result = _load_knowledge_sync(filepath)
    assert result is None


# Test _load_knowledge_sync invalid json
def test_load_knowledge_sync_invalid_json(temp_dir):
    """Test loading invalid JSON returns None."""
    filepath = os.path.join(temp_dir, "invalid.json")
    with open(filepath, "w") as f:
        f.write("{ not valid json")

    result = _load_knowledge_sync(filepath)
    assert result is None


# Test async load_knowledge function
@pytest.mark.asyncio
async def test_load_knowledge_async_success(temp_dir):
    """Test async knowledge loading."""
    filepath = os.path.join(temp_dir, "test.json")
    data = {"test": "data"}
    with open(filepath, "w") as f:
        json.dump(data, f)

    result = await load_knowledge(filepath)
    assert result == data


@pytest.mark.asyncio
async def test_load_knowledge_async_not_found(temp_dir):
    """Test async loading of non-existent file."""
    filepath = os.path.join(temp_dir, "missing.json")
    result = await load_knowledge(filepath)
    assert result is None


# Test KnowledgeLoader initialization
def test_knowledge_loader_init(temp_dir):
    """Test KnowledgeLoader initialization."""
    loader = KnowledgeLoader(
        knowledge_data_path=temp_dir, master_knowledge_file="master.json"
    )
    assert loader.knowledge_data_path == temp_dir
    assert loader.master_knowledge_file == os.path.join(temp_dir, "master.json")
    assert isinstance(loader.loaded_knowledge, dict)


# Test KnowledgeLoader load_all with master file
def test_knowledge_loader_load_all_with_master(temp_dir):
    """Test load_all when master file exists."""
    master_file = os.path.join(temp_dir, "master.json")
    master_data = {"master": "data"}
    with open(master_file, "w") as f:
        json.dump(master_data, f)

    loader = KnowledgeLoader(
        knowledge_data_path=temp_dir, master_knowledge_file="master.json"
    )
    loader.load_all()

    assert loader.loaded_knowledge == master_data


# Test KnowledgeLoader load_all without master file (loads individual files)
def test_knowledge_loader_load_all_without_master(temp_dir):
    """Test load_all when no master file exists - loads individual JSON files."""
    # Create some individual knowledge files
    file1_data = {"domain1": {"key1": "value1"}}
    file2_data = {"domain2": {"key2": "value2"}}

    with open(os.path.join(temp_dir, "file1.json"), "w") as f:
        json.dump(file1_data, f)
    with open(os.path.join(temp_dir, "file2.json"), "w") as f:
        json.dump(file2_data, f)

    loader = KnowledgeLoader(
        knowledge_data_path=temp_dir, master_knowledge_file="master.json"
    )
    loader.load_all()

    # Should have canonical data plus loaded files
    assert "SelfFixingEngineer" in loader.loaded_knowledge  # From canonical
    assert "domain1" in loader.loaded_knowledge or "domain2" in loader.loaded_knowledge


# Test get_knowledge returns a copy
def test_knowledge_loader_get_knowledge_returns_copy(temp_dir):
    """Test that get_knowledge returns a deep copy."""
    loader = KnowledgeLoader(knowledge_data_path=temp_dir)
    loader.loaded_knowledge = {"test": {"nested": "value"}}

    knowledge = loader.get_knowledge()
    knowledge["test"]["nested"] = "modified"

    # Original should be unchanged
    assert loader.loaded_knowledge["test"]["nested"] == "value"


# Test save_current_knowledge
def test_knowledge_loader_save_current_knowledge(temp_dir):
    """Test saving current knowledge."""
    loader = KnowledgeLoader(
        knowledge_data_path=temp_dir, master_knowledge_file="master.json"
    )
    loader.loaded_knowledge = {"test": "data"}
    loader.save_current_knowledge()

    # Verify file was saved
    master_file = os.path.join(temp_dir, "master.json")
    assert os.path.exists(master_file)
    with open(master_file, "r") as f:
        loaded = json.load(f)
    assert loaded == {"test": "data"}


# Test inject_to_arbiter success
def test_inject_to_arbiter_success():
    """Test injecting knowledge into an arbiter."""

    class MockArbiter:
        state = {"memory": {}}
        name = "test_arbiter"

    loader = KnowledgeLoader()
    loader.loaded_knowledge = {"domain": {"key": "value"}}
    arbiter = MockArbiter()
    loader.inject_to_arbiter(arbiter)

    # Knowledge should be merged into memory
    assert "domain" in arbiter.state["memory"]
    assert arbiter.state["memory"]["domain"] == {"key": "value"}


# Test inject_to_arbiter invalid state
def test_inject_to_arbiter_invalid_state():
    """Test inject_to_arbiter with invalid arbiter state."""

    class MockArbiter:
        state = "invalid"

    loader = KnowledgeLoader()
    arbiter = MockArbiter()

    # Should not raise, just log error
    loader.inject_to_arbiter(arbiter)


# Test inject_to_arbiter no state attribute
def test_inject_to_arbiter_no_state():
    """Test inject_to_arbiter when arbiter has no state."""

    class MockArbiter:
        pass

    loader = KnowledgeLoader()
    arbiter = MockArbiter()

    # Should not raise, just log error
    loader.inject_to_arbiter(arbiter)


# Test thread safety in inject_to_arbiter
def test_inject_to_arbiter_thread_safety():
    """Test that inject_to_arbiter is thread safe."""

    class MockArbiter:
        state = {"memory": {}}
        name = "test"

    loader = KnowledgeLoader()
    loader.loaded_knowledge = {"domain": {"key": "value"}}
    arbiter = MockArbiter()

    def inject():
        loader.inject_to_arbiter(arbiter)

    threads = [threading.Thread(target=inject) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Should have the knowledge injected
    assert arbiter.state["memory"]["domain"] == {"key": "value"}


# Test KnowledgeLoader with non-existent knowledge path
def test_knowledge_loader_nonexistent_path(temp_dir):
    """Test loader behavior when knowledge path doesn't exist."""
    nonexistent = os.path.join(temp_dir, "nonexistent")
    loader = KnowledgeLoader(
        knowledge_data_path=nonexistent, master_knowledge_file="master.json"
    )
    loader.load_all()

    # Should still have canonical knowledge
    assert "SelfFixingEngineer" in loader.loaded_knowledge
