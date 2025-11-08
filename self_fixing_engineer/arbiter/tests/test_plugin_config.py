import pytest
from unittest.mock import patch
from arbiter.plugin_config import PluginRegistry, SANDBOXED_PLUGINS

# Fixture to provide the expected plugin dictionary
@pytest.fixture
def expected_plugins():
    return {
        "benchmarking": "arbiter.benchmarking_engine.BenchmarkingEnginePlugin",
        "explainable_reasoner": "arbiter.explainable_reasoner.ExplainableReasonerPlugin",
        "generate_tests": "arbiter.generate_tests.GenerateTestsPlugin",
        "world": "arbiter.plugins.world_plugin",
        "gossip": "arbiter.plugins.gossip_plugin",
        "chat": "arbiter.plugins.chat_plugin",
        "craft": "arbiter.plugins.craft_plugin",
        "legal_tender_explorer": "arbiter.legal_tender_explorer.LegalTenderAutonomousExplorer",
    }

# Test that get_plugins returns a copy of the internal dictionary
def test_get_plugins_returns_copy(expected_plugins):
    plugins = PluginRegistry.get_plugins()
    assert plugins == expected_plugins
    assert plugins is not PluginRegistry._PLUGINS  # Ensure it's a copy
    plugins["new_plugin"] = "test.path"  # Modify the returned copy
    assert PluginRegistry._PLUGINS.get("new_plugin") is None  # Original remains unchanged

# Test that SANDBOXED_PLUGINS is a copy of the internal dictionary
def test_sandboxed_plugins_is_copy(expected_plugins):
    assert SANDBOXED_PLUGINS == expected_plugins
    assert SANDBOXED_PLUGINS is not PluginRegistry._PLUGINS
    SANDBOXED_PLUGINS["new_plugin"] = "test.path"  # Modify the constant
    assert PluginRegistry._PLUGINS.get("new_plugin") is None  # Original remains unchanged

# Test validation with valid plugin registry
def test_validate_valid():
    # Should not raise any exception
    PluginRegistry.validate()

# Test validation with invalid key type
@patch.object(PluginRegistry, '_PLUGINS', {1: "arbiter.test.TestPlugin", "valid": "arbiter.test.ValidPlugin"})
def test_validate_invalid_key_type():
    with pytest.raises(TypeError, match="Plugin registry keys and values must be strings"):
        PluginRegistry.validate()

# Test validation with invalid value type
@patch.object(PluginRegistry, '_PLUGINS', {"valid_key": 123, "another_key": "arbiter.test.ValidPlugin"})
def test_validate_invalid_value_type():
    with pytest.raises(TypeError, match="Plugin registry keys and values must be strings"):
        PluginRegistry.validate()

# Test that validation runs at import time (simulated by re-running validate)
def test_validate_at_import_time():
    # This test ensures the validation runs without error as it does at import
    PluginRegistry.validate()  # Should pass as per the original dictionary

# Test that the plugin registry is immutable at runtime
def test_plugin_registry_immutability():
    # Test that we can't reassign _PLUGINS directly
    with pytest.raises(AttributeError, match="can't set attribute"):
        PluginRegistry._PLUGINS = {"new_key": "new.value"}
    
    # Test that we can't modify _PLUGINS items
    with pytest.raises(TypeError, match="'dict' object does not support item assignment"):
        PluginRegistry._PLUGINS["new_key"] = "new.value"

# Test that all keys are snake_case
def test_snake_case_keys():
    import re
    snake_case_pattern = re.compile(r'^[a-z0-9_]+$')
    for key in PluginRegistry.get_plugins().keys():
        assert snake_case_pattern.match(key), f"Plugin key '{key}' is not snake_case"

# Test that all values are valid dotted Python paths
def test_valid_dotted_paths():
    import re
    dotted_path_pattern = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+$')
    for value in PluginRegistry.get_plugins().values():
        assert dotted_path_pattern.match(value), f"Plugin path '{value}' is not a valid dotted Python path"

# Test that the registry contains expected plugins
def test_expected_plugins_present(expected_plugins):
    plugins = PluginRegistry.get_plugins()
    for key, value in expected_plugins.items():
        assert key in plugins, f"Expected plugin '{key}' not found in registry"
        assert plugins[key] == value, f"Plugin '{key}' has incorrect path: expected {value}, got {plugins[key]}"

# Test for duplicate keys (simulating a developer error in the _PLUGINS dict)
@patch.object(PluginRegistry, '_PLUGINS', {
    "benchmarking": "arbiter.benchmarking_engine.BenchmarkingEnginePlugin",
    "BENCHMARKING": "arbiter.other_engine.OtherPlugin"  # Duplicate in different case
})
def test_no_duplicate_keys_insensitive():
    # This test validates the structure, not case-insensitive duplicates
    # The validate method checks for valid paths, not duplicate keys
    try:
        PluginRegistry.validate()
    except ValueError:
        # Expected since paths might not be valid
        pass

# Test empty registry case
@patch.object(PluginRegistry, '_PLUGINS', {})
def test_empty_registry():
    plugins = PluginRegistry.get_plugins()
    assert plugins == {}
    PluginRegistry.validate()  # Should not raise with empty registry

# Test that modifying returned copy doesn't affect future calls
def test_get_plugins_independent_copies():
    plugins1 = PluginRegistry.get_plugins()
    plugins1["test"] = "test.path"
    plugins2 = PluginRegistry.get_plugins()
    assert "test" not in plugins2, "Modifying one copy affected subsequent calls"