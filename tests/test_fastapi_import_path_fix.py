# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test for FastAPI import path computation fix in testgen_agent.

This test verifies that the import path computation correctly strips
the "generated/<project>/" prefix to generate correct module imports.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Force TESTING mode before any other imports
os.environ["TESTING"] = "1"


@pytest.mark.asyncio
async def test_fastapi_import_path_with_generated_prefix():
    """Test that FastAPI tests strip 'generated/<project>/' prefix from imports."""
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test agent
        agent = TestgenAgent(tmpdir)
        
        # Sample FastAPI code
        fastapi_code = """
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/echo")
def echo(data: dict):
    return data
"""
        
        # Test with generated/<project>/ prefix
        file_path = "generated/hello_generator/app/main.py"
        
        # Generate FastAPI tests
        test_content = agent._generate_fastapi_tests(fastapi_code, file_path)
        
        # Verify the import statement strips the prefix
        # Should be "from app.main import app", NOT "from generated.hello_generator.app.main import app"
        assert "from app.main import app" in test_content, \
            f"Expected 'from app.main import app' but got:\n{test_content}"
        assert "from generated." not in test_content, \
            f"Import should not contain 'generated.' prefix:\n{test_content}"
        
        # Verify endpoints are extracted
        assert "test_get_health" in test_content or "test_health" in test_content
        assert "test_post_echo" in test_content or "test_echo" in test_content


@pytest.mark.asyncio
async def test_fastapi_import_path_without_generated_prefix():
    """Test that FastAPI tests work correctly without 'generated/' prefix."""
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent
    
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = TestgenAgent(tmpdir)
        
        fastapi_code = """
from fastapi import FastAPI

app = FastAPI()

@app.get("/status")
def status():
    return {"status": "running"}
"""
        
        # Test without generated prefix (direct path)
        file_path = "app/main.py"
        
        test_content = agent._generate_fastapi_tests(fastapi_code, file_path)
        
        # Should still generate correct import
        assert "from app.main import app" in test_content
        assert "test_get_status" in test_content or "test_status" in test_content


@pytest.mark.asyncio
async def test_fastapi_import_path_single_file():
    """Test that FastAPI tests work for single file without package structure."""
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent
    
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = TestgenAgent(tmpdir)
        
        fastapi_code = """
from fastapi import FastAPI

app = FastAPI()

@app.get("/ping")
def ping():
    return {"message": "pong"}
"""
        
        # Test single file (no package structure)
        file_path = "main.py"
        
        test_content = agent._generate_fastapi_tests(fastapi_code, file_path)
        
        # Should generate import without package
        assert "from main import app" in test_content
        assert "test_get_ping" in test_content or "test_ping" in test_content


@pytest.mark.asyncio
async def test_fastapi_import_path_deeply_nested():
    """Test that FastAPI tests work with deeply nested generated paths."""
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent
    
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = TestgenAgent(tmpdir)
        
        fastapi_code = """
from fastapi import FastAPI

app = FastAPI()

@app.get("/api/users")
def list_users():
    return []
"""
        
        # Test with deeply nested path
        file_path = "generated/my_project/src/api/routes.py"
        
        test_content = agent._generate_fastapi_tests(fastapi_code, file_path)
        
        # Should strip "generated/my_project/" and use "src.api.routes"
        assert "from src.api.routes import app" in test_content
        assert "from generated." not in test_content
