# tests/test_security_patch_generator_plugin.py

import pytest
import os
import sys
import json
import tempfile
import shutil
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

# Import the plugin from the correct directory
plugin_paths = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'plugins')),  # /plugins/
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'plugins')),  # /simulation/plugins/
]
for path in plugin_paths:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from security_patch_generator_plugin import (
        plugin_health, generate_security_patch,
        LLMPatchGenConfig, _load_config, _parse_llm_output,
        _validate_vuln_details, _validate_patch_syntax,
        PATCH_GENERATION_ATTEMPTS, PATCH_GENERATION_SUCCESS,
        PATCH_GENERATION_ERRORS,
    )
except ImportError as e:
    print(f"Failed to import security_patch_generator_plugin. Searched in: {plugin_paths}")
    print(f"Error: {e}")
    raise

# ==============================================================================
# Pytest Fixtures for mocking external dependencies and environment
# ==============================================================================

@pytest.fixture(autouse=True)
def mock_external_dependencies():
    """
    Mocks external libraries and environment variables for complete isolation.
    """
    with patch('security_patch_generator_plugin.aiohttp.ClientSession') as mock_aiohttp, \
         patch('security_patch_generator_plugin.Redis') as mock_redis, \
         patch('security_patch_generator_plugin.LANGCHAIN_AVAILABLE', True), \
         patch('security_patch_generator_plugin.PYDANTIC_AVAILABLE', True), \
         patch('security_patch_generator_plugin.TENACITY_AVAILABLE', True):

        # Mock LLM API call
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = AsyncMock(return_value=json.dumps({
            "choices": [{"text": "mocked response"}]
        }))
        
        mock_async_response = AsyncMock()
        mock_async_response.text = "mocked response"
        mock_async_response.generations = [[MagicMock(text="mocked response", generation_info={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})]]
        
        mock_llm_client = MagicMock()
        mock_llm_client.generate_text = AsyncMock(return_value=("mocked response", {"tokens": 15}))
        
        with patch('security_patch_generator_plugin._get_llm_client', new=AsyncMock(return_value=mock_llm_client)):
            yield {
                "mock_aiohttp": mock_aiohttp,
                "mock_redis": mock_redis,
                "mock_llm_client": mock_llm_client,
            }

@pytest.fixture
def mock_config_path():
    """Mocks the config file path for testing."""
    temp_dir = Path(tempfile.mkdtemp())
    config_path = temp_dir / "configs" / "security_patch_gen_config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, "w") as f:
        json.dump({
            "llm_provider_name": "mock-provider",
            "llm_model_name": "mock-model",
            "llm_system_prompt": "You are a test bot."
        }, f)

    # Mock the config file path in the plugin
    with patch('security_patch_generator_plugin.Path') as mock_path:
        mock_path.return_value.parent = temp_dir
        mock_path_instance = MagicMock()
        mock_path_instance.parent = temp_dir
        mock_path_instance.__truediv__ = lambda self, x: temp_dir / x
        mock_path.return_value = mock_path_instance
        
        # Also patch __file__ to ensure correct path resolution
        with patch('security_patch_generator_plugin.__file__', str(temp_dir / "plugin.py")):
            yield config_path
    
    shutil.rmtree(temp_dir)

# ==============================================================================
# Unit Tests for Pydantic Config and Validation
# ==============================================================================

def test_llm_config_validation_success():
    """Test that a valid config is accepted by the Pydantic model."""
    # Test with all defaults
    config = LLMPatchGenConfig(llm_system_prompt="Test prompt")
    assert config.llm_provider_name == "openai"
    assert config.llm_temperature == 0.2  # Default value
    assert config.llm_system_prompt == "Test prompt"

def test_llm_config_invalid_temperature():
    """Test that an invalid temperature value raises a ValidationError."""
    # Only test if Pydantic is available in the plugin
    try:
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LLMPatchGenConfig(llm_temperature=1.5, llm_system_prompt="test")
    except ImportError:
        pytest.skip("Pydantic not available")

def test_validate_vuln_details_success():
    """Test that a safe vulnerability details dictionary passes validation."""
    details = {"type": "SQLi", "severity": "High"}
    assert _validate_vuln_details(details) is True

def test_validate_vuln_details_failure():
    """Test that a vulnerability details dictionary with sensitive data raises an error."""
    # Use a pattern that matches the high-confidence secret patterns
    details = {"type": "SQLi", "api_key": "AKIAIOSFODNN7EXAMPLE"}  # AWS access key pattern
    with pytest.raises(ValueError, match="secret"):
        _validate_vuln_details(details)

# ==============================================================================
# Unit Tests for LLM Output Parsing and Validation
# ==============================================================================

def test_parse_llm_output_diff_format():
    """Test that a unified diff is correctly parsed."""
    diff_output = """
Explanation: This is a fix for CVE-123.
```diff
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
 def main():
-    print('hello world')
+    # This is a safe fix
+    print('hello world safely')
```
"""
    patch_content, explanation, error_msg, is_diff = _parse_llm_output(diff_output, "python")
    
    assert patch_content is not None
    assert "--- a/src/app.py" in patch_content
    assert "+++ b/src/app.py" in patch_content
    assert is_diff is True
    assert "CVE-123" in explanation

def test_parse_llm_output_code_block():
    """Test that a code block is correctly parsed."""
    code_output = """
Here's the fixed code:
```python
def safe_query(user_input):
    # Use parameterized queries
    query = "SELECT * FROM users WHERE id = ?"
    return db.execute(query, (user_input,))
```
This prevents SQL injection.
"""
    patch_content, explanation, error_msg, is_diff = _parse_llm_output(code_output, "python")
    
    assert patch_content is not None
    assert "def safe_query" in patch_content
    assert "parameterized queries" in patch_content
    assert is_diff is False
    assert "prevents sql injection" in explanation.lower() or "this prevents sql injection" in explanation.lower()

def test_parse_llm_output_refusal():
    """Test that LLM refusal is correctly detected."""
    refusal_output = "I cannot generate a safe and effective fix for this issue. Manual review required."
    
    patch_content, explanation, error_msg, is_diff = _parse_llm_output(refusal_output, "python")
    
    assert patch_content is None
    assert "manual" in explanation.lower()
    assert error_msg == "Refusal"
    assert is_diff is False

def test_validate_patch_syntax_python_valid():
    """Test that valid Python code passes syntax validation."""
    valid_code = """
def secure_function(param):
    return param * 2
"""
    is_valid, reason = _validate_patch_syntax(valid_code, "python")
    assert is_valid is True
    assert reason == "validated"

def test_validate_patch_syntax_python_invalid():
    """Test that invalid Python code fails syntax validation."""
    invalid_code = """
def broken_function(
    return "missing closing paren"
"""
    is_valid, reason = _validate_patch_syntax(invalid_code, "python")
    assert is_valid is False
    assert reason == "syntax_error"

def test_validate_patch_syntax_non_python():
    """Test that non-Python code skips validation."""
    javascript_code = "function test() { return 'hello'; }"
    is_valid, reason = _validate_patch_syntax(javascript_code, "javascript")
    assert is_valid is True
    assert reason == "skipped"

# ==============================================================================
# Integration Tests for `generate_security_patch` workflow
# ==============================================================================

@pytest.mark.asyncio
async def test_generate_security_patch_success(mock_external_dependencies):
    """Test successful patch generation."""
    # Configure mock to return a valid patch
    mock_patch_response = """
--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
-query = f"SELECT * FROM users WHERE id = {user_id}"
+query = "SELECT * FROM users WHERE id = ?"
+cursor.execute(query, (user_id,))

Explanation: Fixed SQL injection by using parameterized queries.
"""
    mock_external_dependencies["mock_llm_client"].generate_text = AsyncMock(
        return_value=(mock_patch_response, {"tokens": 50})
    )
    
    vuln_details = {"type": "SQL Injection", "severity": "High"}
    vulnerable_code = 'query = f"SELECT * FROM users WHERE id = {user_id}"'
    
    result = await generate_security_patch(
        vulnerability_details=vuln_details,
        vulnerable_code_snippet=vulnerable_code,
        context={"language": "Python"}
    )
    
    assert result["success"] is True
    assert result["proposed_patch"] is not None
    assert "parameterized queries" in result["explanation"].lower()
    assert result["is_diff"] is True
    assert result["vulnerability_type"] == "SQL Injection"

@pytest.mark.asyncio
async def test_generate_security_patch_llm_refusal(mock_external_dependencies):
    """Test handling of LLM refusal to generate patch."""
    mock_external_dependencies["mock_llm_client"].generate_text = AsyncMock(
        return_value=("I cannot generate a safe and effective fix. Manual fix required.", {"tokens": 10})
    )
    
    vuln_details = {"type": "Malicious Request", "severity": "Critical"}
    vulnerable_code = "exec(user_input)"
    
    result = await generate_security_patch(
        vulnerability_details=vuln_details,
        vulnerable_code_snippet=vulnerable_code
    )
    
    assert result["success"] is False
    assert result["proposed_patch"] is None
    assert "manual" in result["explanation"].lower()

@pytest.mark.asyncio
async def test_generate_security_patch_empty_response(mock_external_dependencies):
    """Test handling of empty LLM response."""
    mock_external_dependencies["mock_llm_client"].generate_text = AsyncMock(
        return_value=("", {})
    )
    
    vuln_details = {"type": "XSS", "severity": "Medium"}
    vulnerable_code = "element.innerHTML = userInput"
    
    result = await generate_security_patch(
        vulnerability_details=vuln_details,
        vulnerable_code_snippet=vulnerable_code
    )
    
    assert result["success"] is False
    assert result["proposed_patch"] is None
    assert "empty content" in result["status_reason"].lower()

@pytest.mark.asyncio
async def test_generate_security_patch_with_cache(mock_external_dependencies):
    """Test that caching is attempted (even if Redis is not available)."""
    # Mock Redis to be unavailable
    with patch('security_patch_generator_plugin.REDIS_AVAILABLE', False):
        mock_external_dependencies["mock_llm_client"].generate_text = AsyncMock(
            return_value=("def fixed(): pass", {"tokens": 5})
        )
        
        vuln_details = {"type": "Test", "severity": "Low"}
        vulnerable_code = "test_code"
        
        # First call
        result1 = await generate_security_patch(
            vulnerability_details=vuln_details,
            vulnerable_code_snippet=vulnerable_code
        )
        
        # Second call (would use cache if available)
        result2 = await generate_security_patch(
            vulnerability_details=vuln_details,
            vulnerable_code_snippet=vulnerable_code
        )
        
        # Both should succeed since we're not actually using cache
        assert result1["success"] is True
        assert result2["success"] is True
        assert result1["cache_hit"] is False
        assert result2["cache_hit"] is False

@pytest.mark.asyncio
async def test_generate_security_patch_invalid_input():
    """Test that invalid input types are rejected."""
    with pytest.raises(TypeError):
        await generate_security_patch(
            vulnerability_details="not a dict",  # Should be dict
            vulnerable_code_snippet="code"
        )
    
    with pytest.raises(TypeError):
        await generate_security_patch(
            vulnerability_details={},
            vulnerable_code_snippet=123  # Should be string
        )

# ==============================================================================
# Unit Tests for Health Check
# ==============================================================================

@pytest.mark.asyncio
async def test_plugin_health_success(mock_external_dependencies):
    """Test that plugin health returns ok when dependencies are available."""
    # Mock config to disable live call
    with patch('security_patch_generator_plugin.LLM_PATCH_GEN_CONFIG') as mock_config:
        mock_config.health_live_call = False
        mock_config.llm_interface_type = "generic_llm_client"
        
        result = await plugin_health()
        
        assert result["status"] in ["ok", "degraded"]
        assert "LLM client interface acquired" in str(result["details"])

@pytest.mark.asyncio
async def test_plugin_health_with_live_call(mock_external_dependencies):
    """Test plugin health with live LLM call enabled."""
    with patch('security_patch_generator_plugin.LLM_PATCH_GEN_CONFIG') as mock_config:
        mock_config.health_live_call = True
        mock_config.llm_interface_type = "generic_llm_client"
        mock_config.llm_timeout_seconds = 5
        
        mock_external_dependencies["mock_llm_client"].generate_text = AsyncMock(
            return_value=("pong", {})
        )
        
        result = await plugin_health()
        
        assert result["status"] == "ok"
        assert "LLM inference test successful" in str(result["details"])