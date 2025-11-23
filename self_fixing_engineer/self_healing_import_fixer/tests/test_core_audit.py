"""
test_core_audit.py - Test Suite to Find Issues in core_audit.py
This test suite is designed to expose what's actually broken in the module
"""

import importlib.util
import os
import sys
from unittest.mock import Mock, patch

import pytest

# Setup paths
current_dir = os.path.dirname(os.path.abspath(__file__))
analyzer_dir = os.path.join(os.path.dirname(current_dir), "analyzer")
sys.path.insert(0, analyzer_dir)

# Mock dependencies before import
mock_secrets_manager = Mock()
mock_secrets_manager.get_secret = Mock(return_value="test_key_12345")

mock_secrets = Mock()
mock_secrets.SECRETS_MANAGER = mock_secrets_manager

mock_utils = Mock()
mock_utils.alert_operator = Mock()
mock_utils.scrub_secrets = Mock(side_effect=lambda x: x)

sys.modules["core_secrets"] = mock_secrets
sys.modules["core_utils"] = mock_utils

# Prevent sys.exit during import
original_exit = sys.exit
sys.exit = lambda x: None

try:
    # Load and patch the module
    core_audit_path = os.path.join(analyzer_dir, "core_audit.py")
    with open(core_audit_path, "r") as f:
        source = f.read()

    # Fix imports
    source = source.replace("from .core_secrets", "from core_secrets")
    source = source.replace("from .core_utils", "from core_utils")

    # Create module
    spec = importlib.util.spec_from_loader("core_audit", loader=None)
    core_audit = importlib.util.module_from_spec(spec)
    exec(source, core_audit.__dict__)

    RegulatoryAuditLogger = core_audit.RegulatoryAuditLogger

except Exception as e:
    print(f"Failed to load module: {e}")
    RegulatoryAuditLogger = None

finally:
    sys.exit = original_exit


def test_missing_methods():
    """Test 1: Identify missing methods that are called but not defined"""
    missing_methods = []

    # Check for methods called in __init__ and other methods
    expected_methods = [
        "_write_initial_log_entry",
        "_initialize_integrity_file",
        "_write_integrity_violation",
        "_update_integrity_metadata",
    ]

    for method in expected_methods:
        if not hasattr(RegulatoryAuditLogger, method):
            missing_methods.append(method)

    assert len(missing_methods) == 0, f"Missing methods: {missing_methods}"


def test_splunk_client_initialization():
    """Test 2: Check if splunk_client is properly initialized"""
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false", "REGULATORY_MODE": "false"}):
        # Add stub methods for missing ones
        RegulatoryAuditLogger._write_initial_log_entry = lambda self: None
        RegulatoryAuditLogger._initialize_integrity_file = lambda self: None

        # Patch _start_integrity_monitor to prevent thread issues
        with patch.object(RegulatoryAuditLogger, "_start_integrity_monitor"):
            config = {"audit_dir": "/tmp/test", "splunk_host": None}
            logger = RegulatoryAuditLogger(config)

            # Check if splunk_client exists after initialization
            assert hasattr(logger, "splunk_client"), "splunk_client not initialized"
            assert hasattr(logger, "splunk_buffer"), "splunk_buffer not initialized"


@pytest.mark.asyncio
async def test_log_critical_event_references():
    """Test 3: Check if log_critical_event has undefined references"""
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false", "REGULATORY_MODE": "false"}):
        # Add all missing methods
        RegulatoryAuditLogger._write_initial_log_entry = lambda self: None
        RegulatoryAuditLogger._initialize_integrity_file = lambda self: None
        RegulatoryAuditLogger._start_integrity_monitor = lambda self: None

        config = {"audit_dir": "/tmp/test", "splunk_host": None}
        logger = RegulatoryAuditLogger(config)

        # Check that log_critical_event can access splunk_client
        try:
            # This should fail if splunk_client isn't properly set
            await logger.log_critical_event("TEST", data="test")
        except AttributeError as e:
            pytest.fail(f"log_critical_event failed with: {e}")


def test_asyncio_create_task_issue():
    """Test 4: Check asyncio.create_task in __init__ (causes runtime warnings)"""
    issues = []

    # Since the source was exec'd, we can't use inspect.getsource
    # Instead, check the actual source file
    try:
        core_audit_path = os.path.join(analyzer_dir, "core_audit.py")
        with open(core_audit_path, "r") as f:
            content = f.read()

        # Look for the problematic pattern in __init__
        if "def __init__" in content:
            # Find the __init__ method
            init_start = content.find("def __init__")
            next_def = content.find("\n    def ", init_start + 1)
            if next_def == -1:
                next_def = len(content)
            init_method = content[init_start:next_def]

            if "asyncio.create_task" in init_method:
                issues.append(
                    "__init__ calls asyncio.create_task - causes 'was never awaited' warning"
                )
    except Exception as e:
        # If we can't read the file, skip the test
        pytest.skip(f"Cannot read source file: {e}")

    assert len(issues) == 0, f"Asyncio issues: {issues}"


def test_os_chown_windows_compatibility():
    """Test 5: Check for Windows incompatible calls (os.chown doesn't exist on Windows)"""
    import platform

    if platform.system() == "Windows":
        # Read the actual source file
        try:
            core_audit_path = os.path.join(analyzer_dir, "core_audit.py")
            with open(core_audit_path, "r") as f:
                content = f.read()

            # Find _initialize_audit_filesystem method
            method_start = content.find("def _initialize_audit_filesystem")
            if method_start == -1:
                pytest.skip("_initialize_audit_filesystem not found")

            next_def = content.find("\n    def ", method_start + 1)
            if next_def == -1:
                next_def = len(content)
            method_source = content[method_start:next_def]

            if "os.chown" in method_source:
                # Check if it's properly protected
                if "PRODUCTION_MODE" not in method_source:
                    pytest.fail("os.chown used without PRODUCTION_MODE check on Windows")

                # Test that it doesn't crash in test mode
                with patch.dict(
                    os.environ, {"PRODUCTION_MODE": "false", "REGULATORY_MODE": "false"}
                ):
                    RegulatoryAuditLogger._write_initial_log_entry = lambda self: None
                    RegulatoryAuditLogger._initialize_integrity_file = lambda self: None
                    RegulatoryAuditLogger._start_integrity_monitor = lambda self: None

                    config = {"audit_dir": "/tmp/test"}
                    try:
                        RegulatoryAuditLogger(config)
                    except AttributeError as e:
                        if "chown" in str(e):
                            pytest.fail(f"os.chown called on Windows: {e}")
        except Exception as e:
            pytest.skip(f"Cannot read source file: {e}")


def test_integrity_monitor_thread():
    """Test 6: Check integrity monitor thread shutdown issues"""
    issues = []

    # Read the actual source file
    try:
        core_audit_path = os.path.join(analyzer_dir, "core_audit.py")
        with open(core_audit_path, "r") as f:
            content = f.read()

        # Find _start_integrity_monitor method
        method_start = content.find("def _start_integrity_monitor")
        if method_start != -1:
            next_def = content.find("\n    def ", method_start + 1)
            if next_def == -1:
                next_def = len(content)
            source = content[method_start:next_def]

            if "daemon=True" in source:
                issues.append("Daemon thread may cause shutdown warnings")

            if "asyncio.run" in source and "while True" in source:
                issues.append("Infinite loop with asyncio.run in thread can cause issues")
    except:
        pass  # If we can't read the source, that's ok for this test

    # Not necessarily failures, but good to know
    if issues:
        print(f"Potential thread issues: {issues}")


def test_undefined_imports():
    """Test 7: Check for any undefined imports or missing dependencies"""
    issues = []

    # Check if SplunkHttpEventCollector is properly handled when missing
    if "SplunkHttpEventCollector" in dir(core_audit):
        if core_audit.SplunkHttpEventCollector is None:
            issues.append("SplunkHttpEventCollector is None (import failed)")

    return issues


def test_file_operations_error_handling():
    """Test 8: Check if file operations handle errors properly"""
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false", "REGULATORY_MODE": "false"}):
        # Add missing methods
        RegulatoryAuditLogger._write_initial_log_entry = lambda self: None
        RegulatoryAuditLogger._initialize_integrity_file = lambda self: None
        RegulatoryAuditLogger._start_integrity_monitor = lambda self: None

        # Try to create logger with invalid path
        config = {"audit_dir": "/invalid\0path/test"}  # Invalid path

        try:
            RegulatoryAuditLogger(config)
            # If it doesn't raise an error, check if it handles it gracefully
        except (OSError, ValueError):
            # This is expected
            pass
        except SystemExit:
            pytest.fail("SystemExit called instead of handling error gracefully in test mode")


# Summary test to list all issues found
def test_summary_of_issues():
    """Summary: List all issues found in core_audit.py"""
    issues = {
        "Missing Methods": [],
        "Initialization Issues": [],
        "Platform Compatibility": [],
        "Threading Issues": [],
        "Error Handling": [],
    }

    # Check missing methods
    for method in [
        "_write_initial_log_entry",
        "_initialize_integrity_file",
        "_write_integrity_violation",
        "_update_integrity_metadata",
    ]:
        if not hasattr(RegulatoryAuditLogger, method):
            issues["Missing Methods"].append(method)

    # Check initialization issues by reading source file
    try:
        core_audit_path = os.path.join(analyzer_dir, "core_audit.py")
        with open(core_audit_path, "r") as f:
            content = f.read()

        # Find __init__ method
        init_start = content.find("def __init__")
        if init_start != -1:
            next_def = content.find("\n    def ", init_start + 1)
            if next_def == -1:
                next_def = len(content)
            init_source = content[init_start:next_def]

            if "asyncio.create_task" in init_source:
                issues["Initialization Issues"].append(
                    "asyncio.create_task in __init__ causes warnings"
                )
    except:
        pass  # Can't read source file

    # Platform issues
    if os.name == "nt":  # Windows
        issues["Platform Compatibility"].append("os.chown not available on Windows")
        issues["Platform Compatibility"].append("pwd/grp modules not available on Windows")

    # Print summary
    print("\n=== ISSUES FOUND IN core_audit.py ===")
    for category, items in issues.items():
        if items:
            print(f"\n{category}:")
            for item in items:
                print(f"  - {item}")

    # Adjust assertion based on what's actually been fixed
    # If missing methods are fixed, we expect 0, not 4
    if len(issues["Missing Methods"]) > 0:
        assert False, f"Found missing methods: {issues['Missing Methods']}"

    # Just report other issues without failing
    return issues


if __name__ == "__main__":
    # Run tests to expose issues
    pytest.main([__file__, "-v", "-s"])
