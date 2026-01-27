"""
Test to verify the analyzer module mock fix works correctly in pytest.
"""

import sys
import importlib.util
import pytest


def test_analyzer_modules_are_mocked():
    """Test that analyzer and its submodules are properly mocked."""
    # Test that analyzer modules can be found using find_spec
    # This is exactly what compat_core.py does at line 1023
    spec = importlib.util.find_spec("analyzer.core_utils")
    assert spec is not None, "analyzer.core_utils should be found by find_spec()"
    
    spec = importlib.util.find_spec("analyzer.core_audit")
    assert spec is not None, "analyzer.core_audit should be found by find_spec()"
    
    spec = importlib.util.find_spec("analyzer.core_secrets")
    assert spec is not None, "analyzer.core_secrets should be found by find_spec()"


def test_analyzer_modules_in_sys_modules():
    """Test that the modules are in sys.modules."""
    assert "analyzer" in sys.modules, "analyzer should be in sys.modules"
    assert "analyzer.core_utils" in sys.modules, "analyzer.core_utils should be in sys.modules"
    assert "analyzer.core_audit" in sys.modules, "analyzer.core_audit should be in sys.modules"
    assert "analyzer.core_secrets" in sys.modules, "analyzer.core_secrets should be in sys.modules"


def test_analyzer_core_utils_attributes():
    """Test that core_utils has required attributes."""
    core_utils = sys.modules["analyzer.core_utils"]
    assert hasattr(core_utils, "alert_operator"), "core_utils should have alert_operator"
    assert hasattr(core_utils, "scrub_secrets"), "core_utils should have scrub_secrets"
    
    # Test that the mock functions work
    result = core_utils.alert_operator("test message", level="INFO")
    assert result is None, "alert_operator should return None"
    
    test_value = "test_value"
    result = core_utils.scrub_secrets(test_value)
    assert result == test_value, "scrub_secrets should return input unchanged"


def test_analyzer_core_audit_attributes():
    """Test that core_audit has required attributes."""
    core_audit = sys.modules["analyzer.core_audit"]
    assert hasattr(core_audit, "audit_logger"), "core_audit should have audit_logger"
    assert hasattr(core_audit, "get_audit_logger"), "core_audit should have get_audit_logger"
    
    # Test MockAuditLogger
    audit_logger = core_audit.get_audit_logger()
    audit_logger.log_event("test_event", key="value")


def test_analyzer_core_secrets_attributes():
    """Test that core_secrets has required attributes."""
    core_secrets = sys.modules["analyzer.core_secrets"]
    assert hasattr(core_secrets, "SECRETS_MANAGER"), "core_secrets should have SECRETS_MANAGER"
    
    # Test MockSecretsManager
    secret = core_secrets.SECRETS_MANAGER.get_secret("test_key")
    assert secret == "mock_secret_value", "SECRETS_MANAGER.get_secret should return mock value"


def test_compat_core_can_import_without_analyzer_error():
    """Test that compat_core.py can be imported without ModuleNotFoundError for analyzer."""
    try:
        from self_fixing_engineer.self_healing_import_fixer.import_fixer import compat_core
        # If we get here, the import succeeded (which is what we want)
        assert True
    except ModuleNotFoundError as e:
        # If we get a ModuleNotFoundError, make sure it's not for 'analyzer'
        if "analyzer" in str(e):
            pytest.fail(f"compat_core.py still has analyzer import error: {e}")
        # Other import errors are acceptable for this test
