# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# -*- coding: utf-8 -*-
"""
test_language_aware_validation.py
Test suite for language-aware validation in validate_generated_project.

Tests that the validation logic correctly handles different programming languages:
- Python (default behavior)
- TypeScript/JavaScript
- Java
- Go
- Rust
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from runner.runner_file_utils import validate_generated_project


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.asyncio
async def test_python_validation_with_main_py(temp_dir):
    """Test Python project validation with main.py."""
    # Create a simple Python project
    main_py = temp_dir / "main.py"
    main_py.write_text("print('hello')")
    
    requirements = temp_dir / "requirements.txt"
    requirements.write_text("fastapi\nuvicorn")
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="python"
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"
    assert result["file_count"] == 2
    assert result["python_files_valid"] == 1
    assert result["python_files_invalid"] == 0


@pytest.mark.asyncio
async def test_python_validation_without_language_param(temp_dir):
    """Test backward compatibility - Python is default when language not specified."""
    # Create a simple Python project
    main_py = temp_dir / "main.py"
    main_py.write_text("print('hello')")
    
    requirements = temp_dir / "requirements.txt"
    requirements.write_text("fastapi\nuvicorn")
    
    result = await validate_generated_project(
        output_dir=temp_dir
        # No language parameter - should default to Python
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"


@pytest.mark.asyncio
async def test_typescript_validation_with_index_ts(temp_dir):
    """Test TypeScript project validation with index.ts and package.json."""
    # Create a TypeScript project
    index_ts = temp_dir / "index.ts"
    index_ts.write_text("console.log('hello');")
    
    package_json = temp_dir / "package.json"
    package_json.write_text('{"name": "test", "version": "1.0.0"}')
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="typescript",
        check_python_syntax=False
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"
    assert result["file_count"] == 2


@pytest.mark.asyncio
async def test_typescript_validation_with_app_ts(temp_dir):
    """Test TypeScript project validation with app.ts as entry point."""
    # Create a TypeScript project with app.ts
    app_ts = temp_dir / "app.ts"
    app_ts.write_text("const app = express();")
    
    package_json = temp_dir / "package.json"
    package_json.write_text('{"name": "test", "version": "1.0.0"}')
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="typescript",
        check_python_syntax=False
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"


@pytest.mark.asyncio
async def test_typescript_validation_missing_package_json(temp_dir):
    """Test TypeScript validation fails without package.json (critical file)."""
    # Create a TypeScript project without package.json
    index_ts = temp_dir / "index.ts"
    index_ts.write_text("console.log('hello');")
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="typescript",
        check_python_syntax=False
    )
    
    # Should fail because package.json is critical for TypeScript
    assert not result["valid"], "Validation should fail without package.json"
    assert any("package.json" in err.lower() for err in result["errors"])


@pytest.mark.asyncio
async def test_javascript_validation_with_index_js(temp_dir):
    """Test JavaScript project validation with index.js and package.json."""
    # Create a JavaScript project
    index_js = temp_dir / "index.js"
    index_js.write_text("console.log('hello');")
    
    package_json = temp_dir / "package.json"
    package_json.write_text('{"name": "test", "version": "1.0.0"}')
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="javascript",
        check_python_syntax=False
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"
    assert result["file_count"] == 2


@pytest.mark.asyncio
async def test_go_validation_with_main_go(temp_dir):
    """Test Go project validation with main.go and go.mod."""
    # Create a Go project
    main_go = temp_dir / "main.go"
    main_go.write_text("package main\n\nfunc main() {}")
    
    go_mod = temp_dir / "go.mod"
    go_mod.write_text("module test\n\ngo 1.21")
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="go",
        check_python_syntax=False
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"


@pytest.mark.asyncio
async def test_go_validation_missing_go_mod(temp_dir):
    """Test Go validation fails without go.mod (critical file)."""
    # Create a Go project without go.mod
    main_go = temp_dir / "main.go"
    main_go.write_text("package main\n\nfunc main() {}")
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="go",
        check_python_syntax=False
    )
    
    # Should fail because go.mod is critical for Go
    assert not result["valid"], "Validation should fail without go.mod"
    assert any("go.mod" in err.lower() for err in result["errors"])


@pytest.mark.asyncio
async def test_java_validation_with_main_java(temp_dir):
    """Test Java project validation with Main.java."""
    # Create a Java project
    main_java = temp_dir / "Main.java"
    main_java.write_text("public class Main { public static void main(String[] args) {} }")
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="java",
        check_python_syntax=False
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"


@pytest.mark.asyncio
async def test_rust_validation_with_main_rs(temp_dir):
    """Test Rust project validation with main.rs and Cargo.toml."""
    # Create a Rust project
    main_rs = temp_dir / "main.rs"
    main_rs.write_text("fn main() {}")
    
    cargo_toml = temp_dir / "Cargo.toml"
    cargo_toml.write_text('[package]\nname = "test"\nversion = "0.1.0"')
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="rust",
        check_python_syntax=False
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"


@pytest.mark.asyncio
async def test_typescript_with_python_files_doesnt_check_syntax(temp_dir):
    """Test that TypeScript projects don't validate Python syntax even if .py files exist."""
    # Create a TypeScript project with a Python file that has syntax errors
    index_ts = temp_dir / "index.ts"
    index_ts.write_text("console.log('hello');")
    
    package_json = temp_dir / "package.json"
    package_json.write_text('{"name": "test", "version": "1.0.0"}')
    
    # Add a Python file with syntax errors
    bad_py = temp_dir / "bad.py"
    bad_py.write_text("def broken syntax here")
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="typescript",
        check_python_syntax=False  # Should be False for TypeScript
    )
    
    # Should still be valid because we're not checking Python syntax
    assert result["valid"], f"Validation should pass - not checking Python syntax for TypeScript project: {result['errors']}"


@pytest.mark.asyncio
async def test_python_syntax_error_detection(temp_dir):
    """Test that Python projects properly detect syntax errors."""
    # Create a Python project with syntax errors
    main_py = temp_dir / "main.py"
    main_py.write_text("def broken syntax here")
    
    requirements = temp_dir / "requirements.txt"
    requirements.write_text("fastapi\nuvicorn")
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="python",
        check_python_syntax=True
    )
    
    # Should fail due to syntax error
    assert not result["valid"], "Validation should fail due to syntax error"
    assert result["python_files_invalid"] == 1
    assert any("syntax error" in err.lower() for err in result["errors"])


@pytest.mark.asyncio
async def test_python_app_layout_detection(temp_dir):
    """Test that Python app/ layout is properly detected and validated."""
    # Create a Python project with app/ layout
    app_dir = temp_dir / "app"
    app_dir.mkdir()
    
    app_main = app_dir / "main.py"
    app_main.write_text("print('hello')")
    
    requirements = temp_dir / "requirements.txt"
    requirements.write_text("fastapi\nuvicorn")
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="python"
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"


@pytest.mark.asyncio
async def test_typescript_does_not_check_requirements_txt(temp_dir):
    """Test that TypeScript projects don't get warnings about missing requirements.txt."""
    # Create a TypeScript project
    index_ts = temp_dir / "index.ts"
    index_ts.write_text("console.log('hello');")
    
    package_json = temp_dir / "package.json"
    package_json.write_text('{"name": "test", "version": "1.0.0"}')
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="typescript",
        check_python_syntax=False
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"
    # Should not have warning about requirements.txt
    assert not any("requirements.txt" in warn.lower() for warn in result.get("warnings", []))


@pytest.mark.asyncio
async def test_case_insensitive_language_matching(temp_dir):
    """Test that language parameter is case-insensitive."""
    # Test with uppercase TypeScript
    index_ts = temp_dir / "index.ts"
    index_ts.write_text("console.log('hello');")
    
    package_json = temp_dir / "package.json"
    package_json.write_text('{"name": "test", "version": "1.0.0"}')
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="TypeScript",  # Mixed case
        check_python_syntax=False
    )
    
    assert result["valid"], f"Validation failed: {result['errors']}"


@pytest.mark.asyncio
async def test_unknown_language_defaults_to_python(temp_dir):
    """Test that unknown languages default to Python behavior."""
    # Create a Python project
    main_py = temp_dir / "main.py"
    main_py.write_text("print('hello')")
    
    requirements = temp_dir / "requirements.txt"
    requirements.write_text("fastapi\nuvicorn")
    
    result = await validate_generated_project(
        output_dir=temp_dir,
        language="unknown_language"  # Not in the language map
    )
    
    # Should default to Python validation
    assert result["valid"], f"Validation failed: {result['errors']}"
