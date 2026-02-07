# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test to verify import fixes for pytest collection.

These tests verify that the functions that were causing import errors
during pytest collection are now properly exported and importable.
"""

import pytest


def test_run_tests_import():
    """Test that run_tests function can be imported from runner_core."""
    from generator.runner.runner_core import run_tests

    # Verify it's callable
    assert callable(run_tests)

    # Verify it's an async function
    import asyncio

    assert asyncio.iscoroutinefunction(run_tests)


def test_count_tokens_import():
    """Test that count_tokens function can be imported from llm_client."""
    from generator.runner.llm_client import count_tokens

    # Verify it's callable
    assert callable(count_tokens)

    # Verify it's an async function
    import asyncio

    assert asyncio.iscoroutinefunction(count_tokens)


def test_count_tokens_module_import():
    """Test that count_tokens is in __all__ exports."""
    from generator.runner import llm_client

    assert "count_tokens" in llm_client.__all__


def test_tracer_import():
    """Test that tracer can be imported from runner package."""
    from generator.runner import tracer

    # Verify tracer exists
    assert tracer is not None


def test_tracer_in_all_exports():
    """Test that tracer is in __all__ exports."""
    from generator import runner

    assert "tracer" in runner.__all__


@pytest.mark.asyncio
async def test_count_tokens_basic_functionality():
    """Test basic functionality of count_tokens."""
    from generator.runner.llm_client import count_tokens

    # Test with a simple string
    result = await count_tokens("hello world", "gpt-4")

    # Should return an integer
    assert isinstance(result, int)

    # Should be greater than 0 for non-empty text
    assert result > 0


@pytest.mark.asyncio
async def test_run_tests_signature():
    """Test that run_tests has the expected signature."""
    from generator.runner.runner_core import run_tests
    import inspect

    # Get function signature
    sig = inspect.signature(run_tests)

    # Verify required parameters exist
    assert "test_files" in sig.parameters
    assert "code_files" in sig.parameters
    assert "temp_path" in sig.parameters

    # Verify default parameters
    assert sig.parameters["language"].default == "python"
    assert sig.parameters["framework"].default == "pytest"
    assert sig.parameters["timeout"].default == 300
