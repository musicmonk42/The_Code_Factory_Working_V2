"""
test_core_policy.py - Test suite for PolicyManager
"""

import pytest
import os
import sys
import json
import hmac
import hashlib
import asyncio
import importlib.util
from unittest.mock import patch, Mock, AsyncMock

# --- Windows event loop compatibility for asyncio.run in pytest ---
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Setup paths
current_dir = os.path.dirname(os.path.abspath(__file__))
analyzer_dir = os.path.join(os.path.dirname(current_dir), "analyzer")
sys.path.insert(0, analyzer_dir)

# Create mock objects
mock_alert_operator = Mock()
mock_scrub_secrets = Mock(side_effect=lambda x: x)
mock_audit_logger = Mock()
mock_audit_logger.log_event = Mock()
mock_secrets_manager = Mock()


# Create mock modules
class MockCoreUtils:
    alert_operator = mock_alert_operator
    scrub_secrets = mock_scrub_secrets


class MockCoreAudit:
    audit_logger = mock_audit_logger


class MockCoreSecrets:
    SECRETS_MANAGER = mock_secrets_manager


# Install mocks before importing
sys.modules["core_utils"] = MockCoreUtils()
sys.modules["core_audit"] = MockCoreAudit()
sys.modules["core_secrets"] = MockCoreSecrets()

# Mock boto3 and redis
mock_boto3 = Mock()
mock_redis = Mock()
mock_redis_async = Mock()
mock_redis_instance = AsyncMock()
mock_redis_instance.ping = AsyncMock()
mock_redis_instance.get = AsyncMock(return_value=None)
mock_redis_instance.setex = AsyncMock()
mock_redis_async.Redis = Mock(return_value=mock_redis_instance)

sys.modules["boto3"] = mock_boto3
sys.modules["botocore.exceptions"] = Mock()
sys.modules["redis"] = mock_redis
sys.modules["redis.asyncio"] = mock_redis_async

# Load core_policy with patched imports
core_policy_path = os.path.join(analyzer_dir, "core_policy.py")

# Read and modify the source
with open(core_policy_path, "r") as f:
    source = f.read()

# Replace relative imports
source = source.replace("from .core_utils", "from core_utils")
source = source.replace("from .core_audit", "from core_audit")
source = source.replace("from .core_secrets", "from core_secrets")

# Create module from modified source
spec = importlib.util.spec_from_loader("core_policy", loader=None)
core_policy_module = importlib.util.module_from_spec(spec)

exec(source, core_policy_module.__dict__)

sys.modules["core_policy"] = core_policy_module

# Extract classes
PolicyManager = core_policy_module.PolicyManager
AnalyzerCriticalError = core_policy_module.AnalyzerCriticalError
PolicyViolation = core_policy_module.PolicyViolation
PolicyRule = core_policy_module.PolicyRule
ArchitecturalPolicy = core_policy_module.ArchitecturalPolicy


def mock_get_policy_hmac_key():
    return b"test_hmac_key_12345"


core_policy_module._get_policy_hmac_key = mock_get_policy_hmac_key
core_policy_module._policy_hmac_key = b"test_hmac_key_12345"


@pytest.fixture
def mock_alert_operator_policy():
    mock_alert_operator.reset_mock()
    return mock_alert_operator


@pytest.fixture
def mock_audit_logger_policy():
    mock_audit_logger.log_event.reset_mock()
    return mock_audit_logger


@pytest.fixture
def mock_secrets_manager_policy():
    mock_secrets_manager.get_secret = Mock(return_value="test_hmac_key_12345")
    return mock_secrets_manager


@pytest.fixture
def policy_file_with_signature(tmp_path):
    policy_data = {
        "version": "1.0",
        "description": "Test policies",
        "policies": [
            {
                "id": "PR001",
                "name": "UI Layer Import Restriction",
                "type": "import_restriction",
                "severity": "high",
                "target_modules": ["ui_layer.*"],
                "deny_imports": ["database.*"],
            },
            {
                "id": "PR002",
                "name": "Core modules must not import UI modules",
                "type": "import_restriction",
                "severity": "critical",
                "target_modules": ["core_logic.*"],
                "deny_imports": ["ui_layer.*"],
            },
            {
                "id": "DL001",
                "name": "Limit dependencies",
                "type": "dependency_limit",
                "severity": "medium",
                "target_modules": ["ui_layer.*"],
                "max_dependencies": 2,
            },
            {
                "id": "CP001",
                "name": "Prevent import cycles",
                "type": "cycle_prevention",
                "severity": "critical",
            },
            {
                "id": "NC001",
                "name": "Module naming convention",
                "type": "naming_convention",
                "severity": "low",
                "pattern": "^[a-z_]+$",
                "apply_to": "modules",
            },
        ],
    }

    test_key = b"test_hmac_key_12345"
    policy_content_str = json.dumps(policy_data, sort_keys=True, ensure_ascii=False)
    signature = hmac.new(
        test_key, policy_content_str.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    policy_data["signature"] = signature
    policy_file = tmp_path / "test_policy.json"
    with open(policy_file, "w") as f:
        json.dump(policy_data, f, indent=2)
    return str(policy_file)


@pytest.fixture
def mock_codebase_data():
    return {
        "code_graph": {
            "ui_layer.display": {"database.connector"},  # Violation of PR001
            "core_logic.processor": {"ui_layer.display"},  # Violation of PR002
            "ui_layer.forms": {"lib.a", "lib.b", "lib.c"},  # Violation of DL001 (3 > 2)
            "cycle_a": {"cycle_b"},
            "cycle_b": {"cycle_a"},
        },
        "module_paths": {
            "ui_layer.display": "/path/to/ui_layer/display.py",
            "core_logic.processor": "/path/to/core_logic/processor.py",
            "ui_layer.forms": "/path/to/ui_layer/forms.py",
            "badCamelCaseModule": "/path/to/badCamelCaseModule.py",
            "good_module_name": "/path/to/good_module_name.py",
        },
        "detected_cycles": [["cycle_a", "cycle_b", "cycle_a"]],
        "dead_nodes": set(),
    }


def test_init_with_valid_policy_succeeds(
    policy_file_with_signature, mock_audit_logger_policy, mock_secrets_manager_policy
):
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        core_policy_module._last_good_policy = None
        core_policy_module._compiled_patterns = {}
        manager = PolicyManager(policy_file_with_signature, enable_hot_reload=False)
        assert core_policy_module._last_good_policy is not None
        assert core_policy_module._last_good_policy.version == "1.0"
        calls = mock_audit_logger_policy.log_event.call_args_list
        assert any(call[0][0] == "policy_integrity_verified" for call in calls)


def test_init_with_missing_file_raises_critical_error():
    with pytest.raises(AnalyzerCriticalError):
        PolicyManager("non_existent_policy.json", enable_hot_reload=False)


def test_init_with_invalid_json_raises_critical_error(tmp_path):
    invalid_file = tmp_path / "invalid.json"
    invalid_file.write_text('{ "policies": [ { } }')  # Invalid JSON
    with pytest.raises(AnalyzerCriticalError):
        PolicyManager(str(invalid_file), enable_hot_reload=False)


def test_init_with_invalid_schema_raises_critical_error(tmp_path):
    invalid_file = tmp_path / "invalid_schema.json"
    policy_data = {
        "version": "1.0",
        "policies": [
            {
                "id": "bad",
                "name": "Bad Rule",
                # Missing required 'type' field
            }
        ],
    }
    invalid_file.write_text(json.dumps(policy_data))
    with pytest.raises(AnalyzerCriticalError) as excinfo:
        PolicyManager(str(invalid_file), enable_hot_reload=False)
    error_str = str(excinfo.value).lower()
    assert "validation failed" in error_str or "signature mismatch" in error_str


def test_init_with_tampered_policy_fails(tmp_path, mock_alert_operator_policy):
    policy_data = {
        "version": "1.0",
        "policies": [
            {
                "id": "TEST001",
                "name": "Test Rule",
                "type": "import_restriction",
                "severity": "low",
            }
        ],
        "signature": "wrong_signature_12345",
    }
    policy_file = tmp_path / "tampered_policy.json"
    policy_file.write_text(json.dumps(policy_data))
    # Patch PRODUCTION_MODE in the module directly (no reload!)
    core_policy_module.PRODUCTION_MODE = True
    with pytest.raises(AnalyzerCriticalError) as excinfo:
        PolicyManager(str(policy_file), enable_hot_reload=False)
    assert "integrity check failed" in str(excinfo.value).lower()


def test_enforce_import_restriction_violations(
    policy_file_with_signature, mock_codebase_data
):
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        core_policy_module._last_good_policy = None
        manager = PolicyManager(policy_file_with_signature, enable_hot_reload=False)
        violations = manager.check_architectural_policies(**mock_codebase_data)
        ui_violations = [v for v in violations if v.rule_id == "PR001"]
        # Print violations for debugging
        print("UI_VIOLATIONS:", ui_violations)
        assert len(ui_violations) > 0
        assert any("ui_layer.display" in v.offending_item for v in ui_violations)
        core_violations = [v for v in violations if v.rule_id == "PR002"]
        assert len(core_violations) > 0
        assert any("core_logic.processor" in v.offending_item for v in core_violations)


def test_enforce_dependency_limit_violations(
    policy_file_with_signature, mock_codebase_data
):
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        core_policy_module._last_good_policy = None
        manager = PolicyManager(policy_file_with_signature, enable_hot_reload=False)
        violations = manager.check_architectural_policies(**mock_codebase_data)
        dep_violations = [v for v in violations if v.rule_id == "DL001"]
        assert len(dep_violations) > 0
        assert any("ui_layer.forms" in v.offending_item for v in dep_violations)
        assert any("exceeding the limit" in v.message for v in dep_violations)


def test_enforce_cycle_prevention_violations(
    policy_file_with_signature, mock_codebase_data
):
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        core_policy_module._last_good_policy = None
        manager = PolicyManager(policy_file_with_signature, enable_hot_reload=False)
        violations = manager.check_architectural_policies(**mock_codebase_data)
        cycle_violations = [v for v in violations if v.rule_id == "CP001"]
        assert len(cycle_violations) > 0
        assert any("cycle" in v.message.lower() for v in cycle_violations)


def test_enforce_naming_convention_violations(
    policy_file_with_signature, mock_codebase_data
):
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        core_policy_module._last_good_policy = None
        manager = PolicyManager(policy_file_with_signature, enable_hot_reload=False)
        violations = manager.check_architectural_policies(**mock_codebase_data)
        naming_violations = [v for v in violations if v.rule_id == "NC001"]
        assert len(naming_violations) > 0
        assert any("badCamelCaseModule" in v.offending_item for v in naming_violations)


def test_enforcement_error_handling(
    policy_file_with_signature, mock_codebase_data, mock_alert_operator_policy
):
    with patch.dict(os.environ, {"PRODUCTION_MODE": "false"}):
        core_policy_module._last_good_policy = None
        manager = PolicyManager(policy_file_with_signature, enable_hot_reload=False)
        with patch.object(
            manager, "_enforce_import_restriction", side_effect=Exception("Test error")
        ):
            violations = manager.check_architectural_policies(**mock_codebase_data)
            error_violations = [
                v for v in violations if "Enforcement error" in v.message
            ]
            assert len(error_violations) > 0
            assert mock_alert_operator_policy.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
