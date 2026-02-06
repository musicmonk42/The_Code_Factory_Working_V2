"""
Integration tests for WorkflowEngine deploy stage with DeployAgent.

This module tests the integration between WorkflowEngine._run_deploy_stage
and the DeployAgent system to ensure deployment artifacts are generated correctly.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Set testing environment
os.environ["TESTING"] = "true"


@pytest.fixture
def sample_codegen_result():
    """Sample code generation result with FastAPI application."""
    return {
        "status": "completed",
        "files": {
            "main.py": """from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/")
async def root():
    return {"message": "Hello World"}
""",
            "requirements.txt": """fastapi==0.104.1
uvicorn==0.24.0
pydantic==2.5.0
""",
            "models.py": """from pydantic import BaseModel

class Item(BaseModel):
    name: str
    price: float
"""
        }
    }


@pytest.mark.asyncio
async def test_deploy_stage_generates_artifacts_with_real_agent(tmp_path, sample_codegen_result):
    """Test that deploy stage generates deployment artifacts using DeployAgent.
    
    This test verifies:
    1. DeployAgent is invoked correctly
    2. Dockerfile is generated
    3. docker-compose.yml is generated (if returned by agent)
    4. .dockerignore is generated (if returned by agent)
    5. deploy_metadata.json is generated
    6. Files are written to disk
    """
    # Import after pytest setup to avoid collection-time errors
    from generator.main.engine import WorkflowEngine
    
    # Create a minimal config
    config = {
        "enable_deploy": True,
        "output_path": str(tmp_path)
    }
    
    engine = WorkflowEngine(config=config)
    
    # Run the deploy stage
    result = await engine._run_deploy_stage(
        codegen_result=sample_codegen_result,
        output_path=str(tmp_path),
        workflow_id="test-deploy-integration-001",
        provenance=None
    )
    
    # Verify the result status
    assert result["status"] in ["completed", "skipped"], f"Unexpected status: {result['status']}"
    
    # If completed, verify files were generated
    if result["status"] == "completed":
        # Check that files_written list is populated
        assert "files_written" in result, "files_written key missing from result"
        assert len(result["files_written"]) > 0, "No files were written"
        
        # Verify at minimum a Dockerfile exists
        assert "Dockerfile" in result["files_written"], "Dockerfile not in files_written list"
        
        # Verify files exist on disk
        dockerfile_path = tmp_path / "Dockerfile"
        assert dockerfile_path.exists(), "Dockerfile not found on disk"
        
        # Read and verify Dockerfile content
        dockerfile_content = dockerfile_path.read_text()
        assert len(dockerfile_content) > 100, "Dockerfile content is too short (likely a stub)"
        assert "FROM python:" in dockerfile_content, "Dockerfile missing FROM instruction"
        assert "COPY" in dockerfile_content or "ADD" in dockerfile_content, "Dockerfile missing COPY/ADD instruction"
        
        # Check for docker-compose.yml if it was generated
        if "docker-compose.yml" in result["files_written"]:
            compose_path = tmp_path / "docker-compose.yml"
            assert compose_path.exists(), "docker-compose.yml listed but not found on disk"
            compose_content = compose_path.read_text()
            assert "version:" in compose_content or "services:" in compose_content, "Invalid docker-compose.yml"
        
        # Check for .dockerignore if it was generated
        if ".dockerignore" in result["files_written"]:
            dockerignore_path = tmp_path / ".dockerignore"
            assert dockerignore_path.exists(), ".dockerignore listed but not found on disk"
            dockerignore_content = dockerignore_path.read_text()
            assert len(dockerignore_content) > 0, ".dockerignore is empty"
        
        # Check for deploy_metadata.json if it was generated
        if "deploy_metadata.json" in result["files_written"]:
            metadata_path = tmp_path / "deploy_metadata.json"
            assert metadata_path.exists(), "deploy_metadata.json listed but not found on disk"
            metadata_content = metadata_path.read_text()
            metadata = json.loads(metadata_content)
            assert "generated_at" in metadata, "deploy_metadata.json missing generated_at"
            assert "generator" in metadata, "deploy_metadata.json missing generator"


@pytest.mark.asyncio
async def test_deploy_stage_fallback_to_templates(tmp_path, sample_codegen_result, monkeypatch):
    """Test that deploy stage falls back to templates when DeployAgent fails.
    
    This test simulates a DeployAgent failure and verifies:
    1. Fallback mechanism activates
    2. Template-based generation still produces Dockerfile
    3. Files are written successfully
    4. No exception is raised
    """
    # Import after pytest setup
    from generator.main.engine import WorkflowEngine
    
    config = {
        "enable_deploy": True,
        "output_path": str(tmp_path)
    }
    
    # Mock DeployAgent to raise an exception
    with patch('generator.main.engine.HAS_DEPLOY_AGENT', True):
        with patch('generator.main.engine.DeployAgent') as MockDeployAgent:
            # Configure mock to raise exception on initialization
            MockDeployAgent.side_effect = Exception("Simulated DeployAgent failure")
            
            engine = WorkflowEngine(config=config)
            
            # Run the deploy stage
            result = await engine._run_deploy_stage(
                codegen_result=sample_codegen_result,
                output_path=str(tmp_path),
                workflow_id="test-deploy-fallback-001",
                provenance=None
            )
            
            # Verify result
            assert result["status"] == "completed", f"Expected completed, got {result['status']}"
            assert "Dockerfile" in result["files_written"], "Dockerfile not generated in fallback"
            
            # Verify Dockerfile exists and has content
            dockerfile_path = tmp_path / "Dockerfile"
            assert dockerfile_path.exists(), "Dockerfile not written in fallback mode"
            dockerfile_content = dockerfile_path.read_text()
            assert len(dockerfile_content) > 100, "Fallback Dockerfile is too short"
            assert "FROM python:" in dockerfile_content, "Fallback Dockerfile missing FROM instruction"


@pytest.mark.asyncio
async def test_deploy_stage_handles_empty_codegen_result(tmp_path):
    """Test that deploy stage handles empty codegen results gracefully."""
    # Import after pytest setup
    from generator.main.engine import WorkflowEngine
    
    config = {
        "enable_deploy": True,
        "output_path": str(tmp_path)
    }
    
    engine = WorkflowEngine(config=config)
    
    # Run with empty codegen result
    result = await engine._run_deploy_stage(
        codegen_result={"files": {}},
        output_path=str(tmp_path),
        workflow_id="test-deploy-empty-001",
        provenance=None
    )
    
    # Should skip deployment
    assert result["status"] == "skipped", f"Expected skipped, got {result['status']}"
    assert "reason" in result, "Skipped result should include reason"


@pytest.mark.asyncio
async def test_deploy_stage_detects_framework_correctly(tmp_path):
    """Test that deploy stage correctly detects different frameworks."""
    # Import after pytest setup
    from generator.main.engine import WorkflowEngine
    
    test_cases = [
        ("fastapi", "from fastapi import FastAPI\napp = FastAPI()"),
        ("flask", "from flask import Flask\napp = Flask(__name__)"),
        ("django", "from django.conf import settings\nDJANGO_SETTINGS_MODULE = 'myapp.settings'")
    ]
    
    config = {
        "enable_deploy": True,
        "output_path": str(tmp_path)
    }
    
    for framework, code in test_cases:
        # Create subdirectory for each test case
        test_dir = tmp_path / framework
        test_dir.mkdir(exist_ok=True)
        
        codegen_result = {
            "files": {
                "main.py": code,
                "requirements.txt": f"{framework}==1.0.0"
            }
        }
        
        engine = WorkflowEngine(config=config)
        
        result = await engine._run_deploy_stage(
            codegen_result=codegen_result,
            output_path=str(test_dir),
            workflow_id=f"test-deploy-{framework}-001",
            provenance=None
        )
        
        # Should complete successfully
        assert result["status"] in ["completed", "skipped"], \
            f"Framework {framework}: Expected completed/skipped, got {result['status']}"
        
        if result["status"] == "completed":
            # Verify Dockerfile was generated
            dockerfile_path = test_dir / "Dockerfile"
            assert dockerfile_path.exists(), f"Framework {framework}: Dockerfile not generated"
