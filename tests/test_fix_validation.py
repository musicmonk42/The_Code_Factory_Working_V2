# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test validation for the 5 interrelated fixes:
1. MAX_PROMPT_TOKENS increased to 32000 (configurable via CODEGEN_MAX_PROMPT_TOKENS env var)
2. Tiktoken encoding uses encoding_for_model with fallback
3. max_tokens increased to 4096 in arbiter config
4. BaseHTTPMiddleware import instructions added
5. Import fixer handles fastapi.middleware.base -> starlette.middleware.base
"""

import pytest
import sys
import os
import re
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_max_prompt_tokens_codegen():
    """Test that MAX_PROMPT_TOKENS is at least 32000 in codegen_prompt.py."""
    filepath = PROJECT_ROOT / 'generator' / 'agents' / 'codegen_agent' / 'codegen_prompt.py'
    with open(filepath, 'r') as f:
        content = f.read()
        match = re.search(r'^MAX_PROMPT_TOKENS\s*=\s*(.+)$', content, re.MULTILINE)
        assert match, "MAX_PROMPT_TOKENS not found in codegen_prompt.py"
        # Value is now configurable via env var; the default must be >= 32000
        # Accept either a bare integer or an int(os.getenv(..., "32000")) expression
        value_str = match.group(1).strip()
        if value_str.isdigit():
            assert int(value_str) >= 32000, f"Expected >= 32000, got {value_str}"
        else:
            assert '32000' in value_str, f"Expected default of 32000 in expression, got: {value_str}"


def test_max_prompt_tokens_critique():
    """Test that MAX_PROMPT_TOKENS is at least 16000 in critique_prompt.py (all occurrences)."""
    filepath = PROJECT_ROOT / 'generator' / 'agents' / 'critique_agent' / 'critique_prompt.py'
    with open(filepath, 'r') as f:
        content = f.read()
        matches = re.findall(r'MAX_PROMPT_TOKENS\s*=\s*(\d+)', content)
        assert len(matches) >= 2, f"Expected at least 2 MAX_PROMPT_TOKENS assignments, found {len(matches)}"
        for val in matches:
            assert int(val) >= 16000, f"Expected >= 16000, got {val}"


def test_tiktoken_encoding_for_model():
    """Test that count_tokens uses encoding_for_model with fallback."""
    filepath = PROJECT_ROOT / 'generator' / 'runner' / 'llm_client.py'
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Check for encoding_for_model usage
    assert 'encoding_for_model' in content, "encoding_for_model not found in llm_client.py"
    assert 'except (KeyError, ValueError)' in content, "Fallback exception handling not found"
    
    # Should have at least 2 occurrences (module-level and class method)
    count = content.count('encoding_for_model')
    assert count >= 2, f"Expected at least 2 occurrences of encoding_for_model, found {count}"


def test_arbiter_max_tokens():
    """Test that max_tokens default is 4096 in arbiter config."""
    filepath = PROJECT_ROOT / 'self_fixing_engineer' / 'arbiter' / 'config.py'
    with open(filepath, 'r') as f:
        content = f.read()
        
    match = re.search(r'max_tokens:\s*int\s*=\s*Field\(\s*default=(\d+)', content)
    assert match, "max_tokens field not found in arbiter config"
    assert int(match.group(1)) == 4096, f"Expected 4096, got {match.group(1)}"


def test_basehttpmiddleware_import_instructions():
    """Test that BaseHTTPMiddleware import instructions are added to codegen."""
    # Check codegen_prompt.py
    filepath = PROJECT_ROOT / 'generator' / 'agents' / 'codegen_agent' / 'codegen_prompt.py'
    with open(filepath, 'r') as f:
        content = f.read()
        assert 'from starlette.middleware.base import BaseHTTPMiddleware' in content, \
            "BaseHTTPMiddleware import instruction not found in codegen_prompt.py"
        assert 'NOT from fastapi.middleware.base' in content, \
            "Warning about incorrect import not found in codegen_prompt.py"
    
    # Check python.jinja2 template
    filepath = PROJECT_ROOT / 'generator' / 'agents' / 'codegen_agent' / 'templates' / 'python.jinja2'
    with open(filepath, 'r') as f:
        content = f.read()
        assert 'from starlette.middleware.base import BaseHTTPMiddleware' in content, \
            "BaseHTTPMiddleware import instruction not found in python.jinja2"
        assert 'NOT from fastapi.middleware.base' in content, \
            "Warning about incorrect import not found in python.jinja2"


def test_basehttpmiddleware_usage_instructions():
    """Test that BaseHTTPMiddleware usage/constructor instructions are in codegen prompts."""
    filepath = PROJECT_ROOT / 'generator' / 'agents' / 'codegen_agent' / 'codegen_prompt.py'
    with open(filepath, 'r') as f:
        content = f.read()
        assert 'app' in content and ('first positional argument' in content.lower() or 'first arg' in content.lower()), \
            "BaseHTTPMiddleware constructor usage instructions not found in codegen_prompt.py"
        assert 'TestClient' in content, \
            "TestClient middleware testing instruction not found in codegen_prompt.py"


def test_import_fixer_basehttpmiddleware():
    """Test that import fixer correctly rewrites fastapi.middleware.base imports."""
    from self_fixing_engineer.self_healing_import_fixer.import_fixer.import_fixer_engine import ImportFixerEngine
    
    test_code = """from fastapi import FastAPI
from fastapi.middleware.base import BaseHTTPMiddleware

app = FastAPI()

class CustomMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return await call_next(request)
"""
    
    fixer = ImportFixerEngine()
    result = fixer.fix_code(test_code)
    
    assert result['status'] == 'success', f"Fix failed: {result.get('message')}"
    assert 'from starlette.middleware.base import BaseHTTPMiddleware' in result['fixed_code'], \
        "Starlette import not found in fixed code"
    assert 'from fastapi.middleware.base' not in result['fixed_code'], \
        "Old fastapi.middleware.base import still present"
    assert len(result['fixes_applied']) > 0, "No fixes recorded in fixes_applied"
    assert 'BaseHTTPMiddleware' in result['fixes_applied'][0], \
        "Fix description doesn't mention BaseHTTPMiddleware"


def test_import_fixer_no_false_positives():
    """Test that import fixer doesn't break code without the problematic import."""
    from self_fixing_engineer.self_healing_import_fixer.import_fixer.import_fixer_engine import ImportFixerEngine
    
    # Code with correct starlette import
    test_code = """from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware

app = FastAPI()
"""
    
    fixer = ImportFixerEngine()
    result = fixer.fix_code(test_code)
    
    assert result['status'] == 'success'
    # Should not modify already-correct code
    assert 'from starlette.middleware.base import BaseHTTPMiddleware' in result['fixed_code']
    # If no other fixes needed, fixed_code should be same as input (or very close)
    lines_input = [l.strip() for l in test_code.strip().split('\n') if l.strip()]
    lines_output = [l.strip() for l in result['fixed_code'].strip().split('\n') if l.strip()]
    assert lines_input == lines_output, "Import fixer modified already-correct code"


def test_depends_ellipsis_warning_in_codegen_prompt():
    """Test that get_syntax_safety_instructions warns against Depends(...) with Ellipsis."""
    filepath = PROJECT_ROOT / 'generator' / 'agents' / 'codegen_agent' / 'codegen_prompt.py'
    with open(filepath, 'r') as f:
        content = f.read()
    assert "Depends(...)" in content, \
        "Depends(...) Ellipsis warning not found in codegen_prompt.py"
    assert "Ellipsis" in content, \
        "Ellipsis explanation not found in codegen_prompt.py"
    assert "6a." in content, \
        "Section 6a (FastAPI Dependency Injection) not found in codegen_prompt.py"
    assert "No `Depends(...)` using Ellipsis literal" in content, \
        "Pre-generation checklist item for Depends(...) not found in codegen_prompt.py"


def test_depends_ellipsis_warning_in_python_template():
    """Test that python.jinja2 warns against Depends(...) with Ellipsis."""
    filepath = PROJECT_ROOT / 'generator' / 'agents' / 'codegen_agent' / 'templates' / 'python.jinja2'
    with open(filepath, 'r') as f:
        content = f.read()
    assert "NEVER USE `Depends(...)` WITH ELLIPSIS" in content, \
        "Depends(...) Ellipsis warning section not found in python.jinja2"
    assert "Ellipsis is not callable" in content, \
        "Ellipsis callable explanation not found in python.jinja2"
    assert "NEVER use `Depends(...)` with Ellipsis literal" in content, \
        "MANDATORY checklist item for Depends(...) not found in python.jinja2"


def test_depends_ellipsis_warning_in_macros_template():
    """Test that _macros.jinja2 warns against Depends(...) with Ellipsis."""
    filepath = PROJECT_ROOT / 'generator' / 'agents' / 'codegen_agent' / 'templates' / '_macros.jinja2'
    with open(filepath, 'r') as f:
        content = f.read()
    assert "NEVER use `Depends(...)` (Ellipsis)" in content, \
        "Depends(...) Ellipsis warning not found in _macros.jinja2"


def test_depends_ellipsis_warning_in_fallback_prompt():
    """Test that codegen_agent.py _build_fallback_prompt warns against Depends(...)."""
    filepath = PROJECT_ROOT / 'generator' / 'agents' / 'codegen_agent' / 'codegen_agent.py'
    with open(filepath, 'r') as f:
        content = f.read()
    assert "NO `Depends(...)` with Ellipsis" in content, \
        "Depends(...) warning not found in _build_fallback_prompt in codegen_agent.py"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
