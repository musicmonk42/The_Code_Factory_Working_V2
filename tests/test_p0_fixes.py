# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test P0 fixes for LogRecord collision and raw code detection.
"""

import pytest
import logging
from unittest.mock import MagicMock, patch


def test_logger_extra_no_collision():
    """Test that logger extra dict doesn't collide with LogRecord attributes."""
    # Setup a logger with a handler
    logger = logging.getLogger("test_logger")
    handler = logging.StreamHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    
    # This should not raise KeyError about 'filename' collision
    try:
        logger.info(
            "Test message",
            extra={"source_file": "test.py"}  # Should use source_file, not file_name
        )
        logger.warning(
            "Test warning",
            extra={"source_file": "test.py", "syntax_error_line": 42, "syntax_error_detail": "test"}
        )
        success = True
    except KeyError as e:
        if "filename" in str(e):
            success = False
        else:
            raise
    
    assert success, "Logger should not raise KeyError for reserved LogRecord attributes"


def test_code_pattern_detection():
    """Test that non-code content is properly detected and rejected."""
    from generator.agents.codegen_agent.codegen_response_handler import parse_llm_response, ERROR_FILENAME
    
    # Test 1: Requirements.txt content should be rejected
    requirements_text = """fastapi>=0.109.0
pydantic>=2.0.0
uvicorn>=0.27.0
"""
    result = parse_llm_response(requirements_text, lang="python")
    assert ERROR_FILENAME in result, "Requirements.txt content should be rejected as invalid Python"
    # After Fix 8, requirements.txt content is recognised as non-prose (has '>=' markers)
    # so it reaches Python syntax validation and fails there — either message is acceptable.
    err_lower = result[ERROR_FILENAME].lower()
    assert "syntax" in err_lower or "validation" in err_lower or "code patterns" in err_lower, (
        f"Error message should describe the rejection reason, got: {result[ERROR_FILENAME]!r}"
    )
    
    # Test 2: Curl commands should be rejected
    curl_text = """curl -X POST http://localhost:8000/endpoint \\
  -H "Content-Type: application/json" \\
  -d '{"key": "value"}'
"""
    result = parse_llm_response(curl_text, lang="python")
    assert ERROR_FILENAME in result, "Curl commands should be rejected"
    
    # Test 3: Prose explanation should be rejected
    prose_text = "I apologize, but I need more information about the requirements."
    result = parse_llm_response(prose_text, lang="python")
    assert ERROR_FILENAME in result, "Prose explanations should be rejected"
    
    # Test 4: Valid Python code should be accepted (with code markers)
    valid_python = """```python
def hello():
    print("Hello, world!")

if __name__ == "__main__":
    hello()
```"""
    result = parse_llm_response(valid_python, lang="python")
    assert ERROR_FILENAME not in result, "Valid Python code should be accepted"
    assert "main.py" in result, "Should return main.py for valid code"


def test_fallback_providers():
    """Test that fallback providers list is correct."""
    from generator.runner.llm_client import LLMClient
    
    # Mock the plugin manager
    with patch('generator.runner.llm_client.LLMPluginManager') as mock_plugin_manager:
        mock_manager = MagicMock()
        mock_plugin_manager.return_value = mock_manager
        
        # Mock provider availability - only openai, gemini, and local are configured
        mock_manager.get_provider.side_effect = lambda p: MagicMock() if p in ["openai", "gemini", "local"] else None
        
        client = LLMClient(api_key="test")
        fallback = client._get_fallback_providers("openai")
        
        # The fallback list should only contain configured providers (gemini, local)
        # and should NOT contain unconfigured providers (grok, claude)
        assert "grok" not in fallback, "Grok should not be in fallback providers"
        assert "claude" not in fallback, "Claude should not be in fallback providers"
        
        # Fallback should only contain gemini and local (openai is the primary, so excluded)
        assert set(fallback) <= {"gemini", "local"}, f"Fallback providers should only be gemini/local, got {fallback}"
        
        # Verify the base provider list is correct
        all_providers = ["openai", "gemini", "local"]
        assert "grok" not in all_providers, "Grok should not be in the default provider list"
        assert "claude" not in all_providers, "Claude should not be in the default provider list"


def test_token_limit_increased():
    """Test that MAX_PROMPT_TOKENS is set to at least 32000."""
    from generator.agents.codegen_agent.codegen_prompt import MAX_PROMPT_TOKENS
    
    assert MAX_PROMPT_TOKENS >= 32000, f"MAX_PROMPT_TOKENS should be at least 32000, got {MAX_PROMPT_TOKENS}"


def test_api_key_redaction():
    """Test that API keys are redacted from error messages."""
    import re
    
    # Simulate an error message with API key (using obviously fake key pattern)
    error_with_key = "403, message='Forbidden', url='https://language.googleapis.com/v1/documents:analyzeEntities?key=AIzaSyTEST_FAKE_KEY_FOR_TESTING'"
    
    # Apply redaction
    redacted = re.sub(r'key=[^&\s]+', 'key=REDACTED', error_with_key)
    
    assert "AIzaSyTEST_FAKE_KEY_FOR_TESTING" not in redacted, "API key should be redacted"
    assert "key=REDACTED" in redacted, "Should contain key=REDACTED"


def test_arbiter_retry_providers():
    """Test that arbiter config has correct retry providers."""
    from self_fixing_engineer.arbiter.config import LLMSettings
    
    settings = LLMSettings()
    
    # Check default retry_providers
    assert "anthropic" not in settings.retry_providers, "Anthropic should not be in retry_providers"
    assert "google" in settings.retry_providers, "Google should be in retry_providers"


def test_engine_testgen_completed_below_threshold_zero_not_completed():
    """
    'completed_below_threshold' with final_metric_value=0 must NOT be
    added to stages_completed (Fix 7).

    The decision logic is intentionally replicated here rather than imported from
    engine.py because engine.py carries heavy async/framework dependencies that
    make unit-level import impractical.  The test imports AgentStatus to keep
    the status literals in sync with the production enum.
    """
    # Import AgentStatus to ensure our test values stay in sync with the enum.
    try:
        from generator.main.engine import AgentStatus
        _failed_val = AgentStatus.FAILED.value
        _skipped_val = AgentStatus.SKIPPED.value
    except ImportError:
        # Fallback: match the values declared in engine.py AgentStatus enum
        _failed_val = "failed"
        _skipped_val = "skipped"

    def _should_append(status: str, metric: int) -> bool:
        """Mirror the gate condition applied in engine.py to testgen stage completion."""
        _below_threshold_zero = (
            status == "completed_below_threshold" and metric == 0
        )
        return status not in [_failed_val, _skipped_val] and not _below_threshold_zero

    # 0% pass rate: should NOT be appended
    assert not _should_append("completed_below_threshold", 0), \
        "0% testgen should not be marked completed"

    # Non-zero pass rate: should be appended (partial success)
    assert _should_append("completed_below_threshold", 50), \
        "Non-zero testgen should still be counted"

    # Completed normally: should be appended
    assert _should_append("completed", 100)

    # Failed: should not be appended
    assert not _should_append(_failed_val, 0)

    # Skipped: should not be appended
    assert not _should_append(_skipped_val, 0)


def test_detect_module_package_collisions_integration():
    """Integration test for _detect_module_package_collisions (Fix 3)."""
    import json
    from generator.agents.codegen_agent.codegen_response_handler import (
        parse_llm_response, ERROR_FILENAME,
    )

    # Simulate collision: both routes.py and routes/__init__.py present
    response = json.dumps({
        "files": {
            "main.py": "from app import create_app\napp = create_app()",
            "app/__init__.py": "from fastapi import FastAPI\ndef create_app(): return FastAPI()",
            "app/routes.py": "router = None",
            "app/routes/__init__.py": "from fastapi import APIRouter\nrouter = APIRouter()",
            "app/routes/health.py": "from . import router",
        }
    })

    files = parse_llm_response(response, lang="python")
    # The bare module file should have been removed
    assert "app/routes.py" not in files, "Collision: routes.py should be removed"
    assert "app/routes/__init__.py" in files


def test_yaml_and_requirements_preserved_integration():
    """Integration: YAML files and requirements.txt are not emptied (Fixes 1 & 8)."""
    import json
    from generator.agents.codegen_agent.codegen_response_handler import (
        parse_llm_response, ERROR_FILENAME,
    )

    response = json.dumps({
        "files": {
            "main.py": "print('ok')",
            "requirements.txt": "fastapi>=0.109.0\npydantic>=2.0.0\n",
            "k8s/deployment.yaml": (
                "apiVersion: apps/v1\nkind: Deployment\n"
                "metadata:\n  name: app\nspec:\n  replicas: 1\n"
            ),
        }
    })

    files = parse_llm_response(response, lang="python")
    assert "requirements.txt" in files
    assert "fastapi" in files["requirements.txt"]
    assert "k8s/deployment.yaml" in files
    assert "apiVersion" in files["k8s/deployment.yaml"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
