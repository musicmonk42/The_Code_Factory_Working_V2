#!/usr/bin/env python3
"""
Test critical production fixes for:
1. Audit crypto rate limiting with caching
2. Test collection validation
3. RST documentation generation
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_secret_caching_enabled():
    """Test that secret caching is enabled in secrets.py"""
    from generator.audit_log.audit_crypto import secrets
    
    # Verify cache data structures exist
    assert hasattr(secrets, '_SECRET_CACHE')
    assert hasattr(secrets, '_SECRET_CACHE_LOCK')
    assert hasattr(secrets, '_SECRET_CACHE_TIMESTAMPS')
    assert hasattr(secrets, 'SECRET_CACHE_TTL_SECONDS')
    
    # Verify TTL is set to a reasonable value (default 300s)
    assert secrets.SECRET_CACHE_TTL_SECONDS > 0
    print(f"✓ Secret caching enabled with TTL: {secrets.SECRET_CACHE_TTL_SECONDS}s")


@pytest.mark.asyncio
async def test_secret_caching_works():
    """Test that secrets are actually cached and reused"""
    from generator.audit_log.audit_crypto import secrets
    
    # Save original secret manager
    original_manager = secrets._secret_manager
    
    try:
        # Create a mock secret manager
        mock_manager = MagicMock()
        mock_manager.get_secret = asyncio.coroutine(lambda x: b"test-secret-value")
        secrets._secret_manager = mock_manager
        
        # Clear the cache
        secrets._SECRET_CACHE.clear()
        secrets._SECRET_CACHE_TIMESTAMPS.clear()
        
        # First call should hit the secret manager
        result1 = await secrets._get_secret_with_retries_and_rate_limit("TEST_SECRET")
        assert result1 == b"test-secret-value"
        assert mock_manager.get_secret.called
        call_count_1 = mock_manager.get_secret.call_count
        
        # Second call should use cache (not call get_secret again)
        result2 = await secrets._get_secret_with_retries_and_rate_limit("TEST_SECRET")
        assert result2 == b"test-secret-value"
        call_count_2 = mock_manager.get_secret.call_count
        
        # Verify cache was used (call count should not increase)
        assert call_count_2 == call_count_1, "Secret should be retrieved from cache"
        print("✓ Secret caching working correctly - cache hit on second call")
        
    finally:
        # Restore original secret manager
        secrets._secret_manager = original_manager
        secrets._SECRET_CACHE.clear()
        secrets._SECRET_CACHE_TIMESTAMPS.clear()


def test_test_file_validation_exists():
    """Test that test file validation is implemented in runner_core"""
    from generator.runner import runner_core
    
    # Check that validation method exists
    assert hasattr(runner_core.Runner, '_validate_test_files')
    print("✓ Test file validation method exists")


def test_test_file_validation_logic():
    """Test the test file validation logic"""
    from generator.runner.runner_core import Runner, RunnerConfig
    
    # Create a minimal runner instance
    config = RunnerConfig(
        backend="local",
        framework="pytest",
        instance_id="test-validation",
        timeout=60
    )
    runner = Runner(config)
    
    # Test with valid pytest files
    valid_files = {
        "test_example.py": "def test_something():\n    assert True",
        "test_another.py": "class TestExample:\n    def test_method(self):\n        pass"
    }
    
    result = runner._validate_test_files(valid_files, "pytest")
    assert len(result["valid_files"]) == 2
    assert len(result["errors"]) == 0
    print(f"✓ Test file validation: {len(result['valid_files'])} valid files found")
    
    # Test with invalid naming
    invalid_files = {
        "example.py": "def test_something():\n    assert True",  # Missing test_ prefix
        "mytest.py": "def something():\n    pass"  # No test function
    }
    
    result = runner._validate_test_files(invalid_files, "pytest")
    assert len(result["warnings"]) > 0  # Should have warnings about naming/content
    print(f"✓ Test file validation: {len(result['warnings'])} warnings for invalid files")


def test_rst_generation_improvements():
    """Test that RST generation has proper indentation"""
    from generator.agents.docgen_agent.docgen_agent import SphinxDocGenerator
    
    # Create a generator with a temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        generator = SphinxDocGenerator(tmpdir)
        
        # Test markdown with code blocks
        markdown_content = """
## Example Section

Some text here.

```python
def example():
    return "test"
```

More text after code.
"""
        
        # Generate RST
        result = asyncio.run(generator.generate_rst(
            content=markdown_content,
            title="Test Document"
        ))
        
        # Check for proper RST code block format
        assert ".. code-block:: python" in result
        assert "def example():" in result
        
        # Check for proper indentation (4 spaces for code)
        lines = result.split('\n')
        code_block_started = False
        for line in lines:
            if ".. code-block::" in line:
                code_block_started = True
            elif code_block_started and "def example():" in line:
                # Should have 4 spaces of indentation
                assert line.startswith("    "), f"Code line should be indented with 4 spaces: '{line}'"
                print("✓ RST code block has proper 4-space indentation")
                break


def test_rst_validation_exists():
    """Test that RST validation is implemented"""
    from generator.agents.docgen_agent.docgen_agent import SphinxDocGenerator
    
    with tempfile.TemporaryDirectory() as tmpdir:
        generator = SphinxDocGenerator(tmpdir)
        
        # Check that validation method exists
        assert hasattr(generator, 'validate_rst')
        
        # Test with valid RST
        valid_rst = """
Test Document
=============

This is a test.
"""
        is_valid, errors = generator.validate_rst(valid_rst)
        assert is_valid or len(errors) == 0  # Should be valid or have no errors
        print(f"✓ RST validation exists and works (valid: {is_valid})")


if __name__ == "__main__":
    print("Running critical fixes tests...\n")
    
    # Run tests
    test_secret_caching_enabled()
    asyncio.run(test_secret_caching_works())
    test_test_file_validation_exists()
    test_test_file_validation_logic()
    test_rst_generation_improvements()
    test_rst_validation_exists()
    
    print("\n✅ All critical fixes tests passed!")
