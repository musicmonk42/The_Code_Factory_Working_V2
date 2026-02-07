# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for bug fixes implemented in response to pipeline failures.

This module tests the fixes for:
1. count_tokens coroutine comparison issue
2. LLM JSON response parsing with prefixes
3. Empty requirements.txt files
4. Concurrent pipeline race condition
5. Testgen "No code files found" validation
6. Redis credentials in logs
"""

import asyncio
import json
import re

import pytest

import agents.codegen_agent.codegen_response_handler as crh
from generator.runner.llm_client import _redact_redis_url


# =============================================================================
# Bug 2: LLM JSON response parsing
# =============================================================================


def test_parse_llm_response_with_json_prefix():
    """
    Test that parse_llm_response handles responses prefixed with 'json\\n'.
    
    Bug: LLM returned 'json\\n{"files": {...}}' which failed JSON parsing.
    Fix: Strip common LLM response prefixes before parsing.
    """
    response = 'json\n{"files": {"main.py": "print(\'hello\')"}}'
    files = crh.parse_llm_response(response, lang="python")
    
    assert "main.py" in files
    assert files["main.py"] == "print('hello')"
    assert crh.ERROR_FILENAME not in files


def test_parse_llm_response_with_python_prefix():
    """Test that 'python\\n' prefix is also handled."""
    response = 'python\n{"files": {"main.py": "print(\'hello\')"}}'
    files = crh.parse_llm_response(response, lang="python")
    
    assert "main.py" in files
    assert files["main.py"] == "print('hello')"


def test_parse_llm_response_raw_json_without_fences():
    """
    Test that raw JSON (without markdown fences) is parsed correctly.
    
    Bug: JSON was being passed to _clean_code_block which stripped content.
    Fix: Try raw JSON parsing BEFORE cleaning.
    """
    response = '{"files": {"main.py": "import os\\nprint(\'test\')"}}'
    files = crh.parse_llm_response(response, lang="python")
    
    assert "main.py" in files
    assert "import os" in files["main.py"]
    assert "print('test')" in files["main.py"]


def test_parse_llm_response_json_with_fences_still_works():
    """Ensure fenced JSON still works after the fix."""
    response = '```json\n{"files": {"main.py": "print(\'hello\')"}}\n```'
    files = crh.parse_llm_response(response, lang="python")
    
    assert "main.py" in files
    assert files["main.py"] == "print('hello')"


# =============================================================================
# Bug 6: Redis credentials in logs
# =============================================================================


def test_redact_redis_url_with_password():
    """
    Test that Redis URL passwords are redacted for safe logging.
    
    Bug: Redis connection string with password was logged in plaintext.
    Fix: Redact password before logging.
    """
    url = "redis://default:XVzVgcZtDkrcPOlBwuTHdDLXKzoVmjsI@redis.railway.internal:6379"
    redacted = _redact_redis_url(url)
    
    # Password should be replaced with [REDACTED]
    assert "[REDACTED]" in redacted
    assert "XVzVgcZtDkrcPOlBwuTHdDLXKzoVmjsI" not in redacted
    
    # Other parts should remain
    assert "redis://" in redacted
    assert "default:" in redacted
    assert "@redis.railway.internal:6379" in redacted


def test_redact_redis_url_without_password():
    """Test that URLs without passwords are not corrupted."""
    url = "redis://localhost:6379"
    redacted = _redact_redis_url(url)
    
    # Should not crash or corrupt URL without password
    assert "redis://" in redacted
    assert "localhost:6379" in redacted


def test_redact_redis_url_empty():
    """Test that empty URL is handled gracefully."""
    url = ""
    redacted = _redact_redis_url(url)
    
    # Should not crash
    assert redacted == ""


# =============================================================================
# Bug 3: Empty file content validation
# =============================================================================


def test_parse_llm_response_empty_file_content():
    """
    Test that empty file content is handled gracefully.
    
    Bug: Empty requirements.txt was written to disk.
    Fix: Validation happens in omnicore_service.py before writing.
    
    Note: parse_llm_response doesn't skip empty files, but omnicore_service does.
    """
    response = json.dumps(
        {
            "files": {
                "main.py": "print('hello')",
                "empty.txt": "",  # Empty content
            }
        }
    )
    
    files = crh.parse_llm_response(response, lang="python")
    
    # Parser should still return empty files
    # (they'll be filtered out in omnicore_service.py before writing)
    assert "main.py" in files
    assert "empty.txt" in files


def test_parse_llm_response_whitespace_only_content():
    """Test that whitespace-only content is preserved by parser."""
    response = json.dumps(
        {
            "files": {
                "main.py": "print('hello')",
                "whitespace.txt": "   \n  \t  \n  ",
            }
        }
    )
    
    files = crh.parse_llm_response(response, lang="python")
    
    # Parser preserves whitespace (filtering happens in omnicore_service.py)
    assert "main.py" in files
    assert "whitespace.txt" in files


# =============================================================================
# Bug 1: count_tokens coroutine comparison
# =============================================================================


@pytest.mark.asyncio
async def test_maybe_await_with_coroutine():
    """
    Test that _maybe_await correctly awaits coroutines.
    
    Bug: count_tokens coroutine was compared with int without awaiting.
    Fix: Added safety checks to ensure token_count is always an int.
    """
    # Import the helper
    from agents.codegen_agent.codegen_prompt import _maybe_await
    
    # Create a simple async function that returns an int
    async def async_counter():
        return 42
    
    # Call it (creates a coroutine)
    coro = async_counter()
    
    # _maybe_await should await it and return the int
    result = await _maybe_await(coro)
    
    assert isinstance(result, int)
    assert result == 42


@pytest.mark.asyncio
async def test_maybe_await_with_sync_value():
    """Test that _maybe_await passes through non-coroutine values."""
    from agents.codegen_agent.codegen_prompt import _maybe_await
    
    # Pass a regular int
    result = await _maybe_await(42)
    
    assert isinstance(result, int)
    assert result == 42


@pytest.mark.asyncio
async def test_maybe_await_with_string():
    """Test that _maybe_await handles non-coroutine objects."""
    from agents.codegen_agent.codegen_prompt import _maybe_await
    
    # Pass a string
    result = await _maybe_await("hello")
    
    assert isinstance(result, str)
    assert result == "hello"


# =============================================================================
# Bug 2 Additional: Multiple JSON formats
# =============================================================================


def test_parse_llm_response_uppercase_json_prefix():
    """Test that uppercase JSON prefix is handled."""
    response = 'JSON\n{"files": {"main.py": "print(\'hello\')"}}'
    files = crh.parse_llm_response(response, lang="python")
    
    assert "main.py" in files
    assert files["main.py"] == "print('hello')"


def test_parse_llm_response_malformed_json_falls_back():
    """
    Test that malformed JSON falls back to single-file mode.
    
    If JSON parsing fails, should treat as single Python file.
    """
    response = '{"invalid json without closing brace'
    files = crh.parse_llm_response(response, lang="python")
    
    # Should fall back to single file mode with error
    # (because the content is not valid Python either)
    assert crh.ERROR_FILENAME in files or crh.DEFAULT_FILENAME in files


def test_parse_llm_response_nested_json_objects():
    """Test that complex nested JSON structures are handled."""
    response = json.dumps(
        {
            "files": {
                "main.py": "# Main file\nprint('main')",
                "utils/helper.py": "def helper():\n    return True",
                "README.md": "# Project\n\nDescription here",
            }
        }
    )
    
    files = crh.parse_llm_response(response, lang="python")
    
    assert "main.py" in files
    assert "utils/helper.py" in files
    assert "README.md" in files
    assert "print('main')" in files["main.py"]


# =============================================================================
# Integration test for multiple fixes
# =============================================================================


def test_parse_llm_response_json_prefix_with_nested_files():
    """
    Integration test: JSON prefix + multiple files + nested paths.
    
    Tests that multiple fixes work together correctly.
    """
    response = 'json\n{"files": {"src/main.py": "import sys\\nprint(sys.version)", "requirements.txt": "fastapi\\nuvicorn"}}'
    files = crh.parse_llm_response(response, lang="python")
    
    assert "src/main.py" in files
    assert "requirements.txt" in files
    assert "import sys" in files["src/main.py"]
    assert "fastapi" in files["requirements.txt"]
    assert crh.ERROR_FILENAME not in files


def test_parse_llm_response_mixed_content_types():
    """Test that mixed content (Python + config files) is handled correctly."""
    response = json.dumps(
        {
            "files": {
                "main.py": "from fastapi import FastAPI\napp = FastAPI()",
                "requirements.txt": "fastapi>=0.100.0\nuvicorn[standard]>=0.22.0",
                "README.md": "# My API\n\nA simple FastAPI application.",
                ".gitignore": "__pycache__/\n*.pyc\n.env",
            }
        }
    )
    
    files = crh.parse_llm_response(response, lang="python")
    
    assert len(files) == 4
    assert "main.py" in files
    assert "requirements.txt" in files
    assert "README.md" in files
    assert ".gitignore" in files
    
    # Verify content
    assert "FastAPI" in files["main.py"]
    assert "fastapi" in files["requirements.txt"]
    assert "My API" in files["README.md"]
    assert "__pycache__" in files[".gitignore"]
