import asyncio
import tempfile
import json
import os
from unittest.mock import patch, AsyncMock, MagicMock, PropertyMock
import pytest

from arbiter.policy.config import get_config
from arbiter.policy.core import (
    initialize_policy_engine, 
    should_auto_learn, 
    reset_policy_engine, 
    get_policy_engine_instance
)


@pytest.fixture
def mock_config(monkeypatch):
    """Creates a mock configuration for testing."""
    # Create temp files for paths
    policy_file = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
    audit_file = tempfile.NamedTemporaryFile(delete=False, suffix='.log')
    
    # Create a simple mock config object
    mock_config = MagicMock()
    mock_config.POLICY_CONFIG_FILE_PATH = policy_file.name
    mock_config.LLM_POLICY_EVALUATION_ENABLED = False
    mock_config.ENCRYPTION_KEY = "8TOLo9wUnAz_6Tew0FPEGtI25-3L52L2hYSqk4eRTXI="
    mock_config.DECISION_OPTIMIZER_SETTINGS = {
        "llm_call_latency_buckets": (0.1, 0.5, 1, 2, 5, 10, 30, 60),
        "feedback_processing_buckets": (0.001, 0.01, 0.1, 1, 10),
        "score_rules": {
            "login_attempts_penalty": -0.2,
            "device_trusted_bonus": 0.3,
            "recent_login_bonus": 0.1,
            "admin_user_bonus": 0.2,
            "default_score": 0.5
        }
    }
    mock_config.CIRCUIT_BREAKER_VALIDATION_ERROR_INTERVAL = 300.0
    mock_config.CIRCUIT_BREAKER_MIN_OPERATION_INTERVAL = 0.1
    mock_config.CIRCUIT_BREAKER_MAX_PROVIDERS = 1000
    mock_config.POLICY_REFRESH_INTERVAL_SECONDS = 300
    mock_config.LLM_API_FAILURE_THRESHOLD = 3
    mock_config.LLM_API_BACKOFF_MAX_SECONDS = 60.0
    mock_config.LLM_PROVIDER = "openai"
    mock_config.LLM_MODEL = "gpt-4o-mini"
    mock_config.LLM_API_TIMEOUT_SECONDS = 30.0
    mock_config.VALID_DOMAIN_PATTERN = r"^[a-zA-Z0-9_.-]+$"
    mock_config.DEFAULT_AUTO_LEARN_POLICY = True
    mock_config.AUDIT_LOG_FILE_PATH = audit_file.name
    mock_config.POLICY_PAUSE_POLLING_INTERVAL = 60.0
    mock_config.ROLE_MAPPINGS = {
        "admin": ["admin", "user", "explorer_user"],
        "auditor": ["auditor", "user"],
        "explorer_user": ["explorer_user", "user"],
        "guest": ["guest"],
        "*": ["user"]
    }
    mock_config.LLM_POLICY_MIN_TRUST_SCORE = 0.5
    mock_config.LLM_VALID_RESPONSES = ["YES", "NO"]
    
    # Add method for API key retrieval
    mock_config.get_api_key_for_provider = MagicMock(return_value="dummy_key")
    
    # Patch get_config to return our mock
    monkeypatch.setattr("arbiter.policy.core.get_config", lambda: mock_config)
    monkeypatch.setattr("arbiter.policy.config.get_config", lambda: mock_config)
    
    # Clean up temp files on test completion
    yield mock_config
    
    try:
        os.unlink(policy_file.name)
        os.unlink(audit_file.name)
    except:
        pass


@pytest.mark.asyncio
async def test_end_to_end_policy_lifecycle(monkeypatch, tmp_path, mock_config):
    """Tests the complete policy engine lifecycle including evaluation, compliance, and refresh."""
    
    # Prepare minimal config/policy file
    policy_path = tmp_path / "policies.json"
    policy = {
        "domain_rules": {"test": {"allow": True, "reason": "Testing"}},
        "user_rules": {"*": {"allow": True, "reason": "Testing"}},
        "llm_rules": {
            "enabled": False, 
            "valid_responses": ["YES", "NO"], 
            "prompt_template": "", 
            "threshold": 0.5, 
            "min_trust_score": 0.1
        },
        "trust_rules": {"enabled": False, "threshold": 0.1},
        "custom_python_rules_enabled": False
    }
    policy_path.write_text(json.dumps(policy))
    
    # Update the mock config with the correct path for this test
    mock_config.POLICY_CONFIG_FILE_PATH = str(policy_path)
    
    # Reset any existing policy engine state
    await reset_policy_engine()
    
    # Create a mock arbiter with required attributes
    mock_arbiter = MagicMock()
    mock_arbiter.plugin_registry = None
    
    # Patch the isinstance check in PolicyEngine.__init__
    # Store the original isinstance function
    original_isinstance = __builtins__['isinstance'] if isinstance(__builtins__, dict) else __builtins__.isinstance
    
    with patch("arbiter.policy.core.isinstance") as mock_isinstance:
        # Make isinstance return True for ArbiterConfig check, use original for others
        def isinstance_side_effect(obj, cls):
            # Import here to avoid circular import
            from arbiter.policy.config import ArbiterConfig
            if cls == ArbiterConfig:
                return True
            # Use the original isinstance for all other checks
            return original_isinstance(obj, cls)
        
        mock_isinstance.side_effect = isinstance_side_effect
        
        # Initialize the policy engine
        initialize_policy_engine(mock_arbiter)

    # Patch external dependencies
    with patch("arbiter.policy.core.audit_log", new_callable=AsyncMock) as mock_audit, \
         patch("arbiter.policy.core.LLMClient") as mock_llm, \
         patch("arbiter.policy.core.is_llm_policy_circuit_breaker_open", return_value=False):

        # Test 1: Simulate happy path - should allow auto-learning
        result, reason = await should_auto_learn("test", "key", "user", {"foo": "bar"})
        assert result is True, f"Expected allow, got deny. Reason: {reason}"
        assert mock_audit.called, "Audit log should have been called"

        # Test 2: Simulate compliance block
        policy_engine = get_policy_engine_instance()
        assert policy_engine is not None, "Policy engine should be initialized"
        
        # Add a compliance control that blocks the action
        policy_engine._compliance_controls = {
            "PC-1": {
                "name": "Test Control",
                "status": "not_implemented", 
                "required": True
            }
        }
        policy_engine._policies["domain_rules"]["test"]["control_tag"] = "PC-1"
        
        result, reason = await should_auto_learn("test", "key", "user", {"foo": "bar"})
        assert result is False, f"Expected deny due to compliance, got allow. Reason: {reason}"
        assert "Compliance enforcement blocked" in reason or "not_implemented" in reason, \
            f"Expected compliance block message, got: {reason}"

        # Test 3: Simulate LLM denial
        # Reset compliance control
        policy_engine._compliance_controls = {}
        policy_engine._policies["domain_rules"]["test"].pop("control_tag", None)
        
        # Enable LLM evaluation and mock a denial
        policy_engine._policies["llm_rules"]["enabled"] = True
        mock_config.LLM_POLICY_EVALUATION_ENABLED = True
        
        instance = mock_llm.return_value
        instance.generate_text = AsyncMock(return_value="NO, Not safe")
        
        result, reason = await should_auto_learn("test", "key", "user", "value")
        assert result is False, f"Expected deny from LLM, got allow. Reason: {reason}"

        # Test 4: Simulate circuit breaker open
        with patch("arbiter.policy.core.is_llm_policy_circuit_breaker_open", return_value=True):
            result, reason = await should_auto_learn("test", "key", "user", "value")
            assert result is False, f"Expected deny from circuit breaker, got allow. Reason: {reason}"
            assert "Circuit breaker open" in reason or "LLM" in reason, \
                f"Expected circuit breaker message, got: {reason}"

        # Test 5: Simulate policy reload
        new_policy = {**policy, "domain_rules": {"test2": {"allow": True, "reason": "Testing2"}}}
        with open(policy_path, "w") as f:
            json.dump(new_policy, f)
        
        # Call reload_policies directly  
        policy_engine.reload_policies()
        
        # Verify the new domain rule was loaded
        assert "test2" in policy_engine._policies["domain_rules"], \
            "New domain rule should be loaded after reload"

        # Test 6: Check metrics were updated (using prometheus_client)
        from prometheus_client import REGISTRY
        
        # Check that expected metrics exist
        metric_names = REGISTRY._names_to_collectors
        expected_metrics = [
            'policy_decisions_total',
            'policy_file_reloads_total'
        ]
        
        for metric_name in expected_metrics:
            assert metric_name in metric_names, \
                f"Expected metric '{metric_name}' not found in registry"
    
    # Cleanup
    await reset_policy_engine()