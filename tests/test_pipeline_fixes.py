# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test fixes for pipeline cascading failures.
"""

import pytest


def test_codegen_prompt_has_dynamic_endpoint_instructions():
    """Test that codegen_prompt.py has dynamic endpoint instructions, not hardcoded."""
    with open("generator/agents/codegen_agent/codegen_prompt.py", "r") as f:
        content = f.read()
    
    # Should NOT have hardcoded test_health.py, test_version.py, test_echo.py
    assert "tests/test_health.py (health endpoint tests)" not in content, \
        "Should not hardcode test_health.py"
    assert "tests/test_version.py (version endpoint tests)" not in content, \
        "Should not hardcode test_version.py"
    assert "tests/test_echo.py (echo endpoint tests if /echo is required)" not in content, \
        "Should not hardcode test_echo.py"
    
    # Should have dynamic instructions
    assert "test_<endpoint>.py" in content or "tests/test_<endpoint>.py" in content, \
        "Should have dynamic endpoint test file instructions"
    assert "from app.main import app" in content, \
        "Should have explicit import instruction for tests"


def test_codegen_prompt_has_staticmethod_warning():
    """Test that codegen_prompt.py warns against @staticmethod with @field_validator."""
    with open("generator/agents/codegen_agent/codegen_prompt.py", "r") as f:
        content = f.read()
    
    # Should have explicit warning about @staticmethod with @field_validator
    assert "@staticmethod" in content and "@field_validator" in content, \
        "Should mention both @staticmethod and @field_validator"
    assert "BREAKS PYDANTIC" in content or "breaks" in content.lower(), \
        "Should warn that @staticmethod breaks Pydantic"


def test_python_template_has_frontend_in_checklist():
    """Test that python.jinja2 includes frontend in checklist when include_frontend is True."""
    with open("generator/agents/codegen_agent/templates/python.jinja2", "r") as f:
        content = f.read()
    
    # Should have conditional frontend checklist items
    assert "{% if include_frontend %}" in content, \
        "Should have conditional frontend section"
    assert "Frontend files included" in content or "frontend files" in content.lower(), \
        "Should mention frontend files in checklist"


def test_python_template_size_reduced():
    """Test that python.jinja2 template has been reduced in size."""
    with open("generator/agents/codegen_agent/templates/python.jinja2", "r") as f:
        lines = f.readlines()
    
    # Template should be significantly smaller (was 877 lines, now should be < 500)
    assert len(lines) < 500, \
        f"Template should be < 500 lines, got {len(lines)}"


def test_testgen_response_handler_has_fix_import_paths():
    """Test that testgen_response_handler.py has fix_import_paths function."""
    with open("generator/agents/testgen_agent/testgen_response_handler.py", "r") as f:
        content = f.read()
    
    # Should have fix_import_paths function that handles "from main import"
    assert "def fix_import_paths" in content, \
        "Should have fix_import_paths function"
    assert "from main import" in content or "from X import" in content, \
        "Should handle import path fixing"
