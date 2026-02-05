"""
Tests for the provenance tracking module.

This module tests:
1. ProvenanceTracker initialization and stage recording
2. SHA256 computation and artifact tracking
3. Overwrite detection
4. Validation functions for calculator API
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from generator.main.provenance import (
    ProvenanceTracker,
    run_fail_fast_validation,
    validate_calculator_routes,
    validate_divide_by_zero_handling,
    validate_requirements_txt,
    validate_syntax,
)


class TestProvenanceTracker:
    """Test cases for ProvenanceTracker class."""

    def test_init_with_custom_job_id(self):
        """Test initialization with custom job ID."""
        tracker = ProvenanceTracker(job_id="test-job-123")
        assert tracker.job_id == "test-job-123"
        assert tracker.stages == []
        assert tracker.artifacts == {}
        assert tracker.errors == []

    def test_init_without_job_id(self):
        """Test initialization generates a job ID."""
        tracker = ProvenanceTracker()
        assert tracker.job_id.startswith("job-")

    def test_compute_sha256(self):
        """Test SHA256 computation."""
        content = "Hello, World!"
        sha256 = ProvenanceTracker.compute_sha256(content)
        # Known SHA256 for "Hello, World!"
        expected = "dffd6021bb2bd5b0af676290809ec3a53191dd81c7f70a4b28688a362182986f"
        assert sha256 == expected

    def test_record_stage_basic(self):
        """Test basic stage recording."""
        tracker = ProvenanceTracker(job_id="test-job")
        
        tracker.record_stage(
            ProvenanceTracker.STAGE_READ_MD,
            artifacts={"input.md": "# Test Content"},
            metadata={"source": "test"}
        )
        
        assert len(tracker.stages) == 1
        stage = tracker.stages[0]
        assert stage["stage"] == "READ_MD"
        assert "timestamp" in stage
        assert "input.md" in stage["artifacts"]
        assert stage["metadata"]["source"] == "test"

    def test_record_stage_artifact_tracking(self):
        """Test artifact history is tracked across stages."""
        tracker = ProvenanceTracker(job_id="test-job")
        
        # Record initial content
        tracker.record_stage(
            ProvenanceTracker.STAGE_READ_MD,
            artifacts={"main.py": "print('v1')"}
        )
        
        # Record modified content
        tracker.record_stage(
            ProvenanceTracker.STAGE_CODEGEN,
            artifacts={"main.py": "print('v2')"}
        )
        
        assert "main.py" in tracker.artifacts
        assert len(tracker.artifacts["main.py"]["history"]) == 2

    def test_record_error(self):
        """Test error recording."""
        tracker = ProvenanceTracker(job_id="test-job")
        
        tracker.record_error(
            stage="TEST_STAGE",
            error_type="TestError",
            message="Test error message",
            details={"key": "value"}
        )
        
        assert len(tracker.errors) == 1
        error = tracker.errors[0]
        assert error["stage"] == "TEST_STAGE"
        assert error["error_type"] == "TestError"
        assert error["message"] == "Test error message"

    def test_check_artifact_changed(self):
        """Test artifact change detection."""
        tracker = ProvenanceTracker(job_id="test-job")
        
        # Same content across stages
        tracker.record_stage("STAGE1", artifacts={"file.py": "content"})
        tracker.record_stage("STAGE2", artifacts={"file.py": "content"})
        assert not tracker.check_artifact_changed("file.py")
        
        # Different content
        tracker.record_stage("STAGE3", artifacts={"file.py": "modified"})
        assert tracker.check_artifact_changed("file.py")

    def test_get_artifact_overwrites(self):
        """Test overwrite detection."""
        tracker = ProvenanceTracker(job_id="test-job")
        
        tracker.record_stage("STAGE1", artifacts={"a.py": "v1", "b.py": "stable"})
        tracker.record_stage("STAGE2", artifacts={"a.py": "v2", "b.py": "stable"})
        
        overwrites = tracker.get_artifact_overwrites()
        assert "a.py" in overwrites
        assert "b.py" not in overwrites

    def test_to_dict(self):
        """Test dictionary conversion."""
        tracker = ProvenanceTracker(job_id="test-job")
        tracker.record_stage("STAGE1", artifacts={"test.py": "content"})
        
        data = tracker.to_dict()
        
        assert data["job_id"] == "test-job"
        assert "started_at" in data
        assert "finished_at" in data
        assert "stages" in data
        assert "summary" in data

    def test_save_to_file(self):
        """Test saving provenance to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = ProvenanceTracker(job_id="test-job")
            tracker.record_stage("STAGE1", artifacts={"test.py": "content"})
            
            path = tracker.save_to_file(tmpdir)
            
            assert Path(path).exists()
            assert "provenance.json" in path
            
            with open(path, "r") as f:
                data = json.load(f)
            
            assert data["job_id"] == "test-job"


class TestValidateCalculatorRoutes:
    """Test cases for calculator route validation."""

    def test_all_routes_present(self):
        """Test when all calculator routes are present."""
        code = '''
from fastapi import FastAPI
app = FastAPI()

@app.post("/api/calculate/add")
def add(a: int, b: int):
    return {"result": a + b}

@app.post("/api/calculate/subtract")
def subtract(a: int, b: int):
    return {"result": a - b}

@app.post("/api/calculate/multiply")
def multiply(a: int, b: int):
    return {"result": a * b}

@app.post("/api/calculate/divide")
def divide(a: int, b: int):
    return {"result": a / b}
'''
        result = validate_calculator_routes(code)
        assert result["valid"] is True
        assert len(result["missing_routes"]) == 0

    def test_missing_routes(self):
        """Test when some routes are missing."""
        code = '''
@app.post("/api/calculate/add")
def add(a: int, b: int):
    return {"result": a + b}
'''
        result = validate_calculator_routes(code)
        assert result["valid"] is False
        assert "/api/calculate/subtract" in result["missing_routes"]

    def test_alternative_route_format(self):
        """Test detection of alternative route formats."""
        code = '''
@app.post("/calculate/add")
@app.post("/calculate/subtract")
@app.post("/calculate/multiply")
@app.post("/calculate/divide")
'''
        result = validate_calculator_routes(code)
        assert result["valid"] is True


class TestValidateDivideByZeroHandling:
    """Test cases for divide-by-zero handling validation."""

    def test_with_http_exception(self):
        """Test code with HTTPException for division by zero."""
        code = '''
from fastapi import HTTPException

@app.post("/api/calculate/divide")
def divide(a: int, b: int):
    if b == 0:
        raise HTTPException(status_code=400, detail="Division by zero")
    return {"result": a / b}
'''
        result = validate_divide_by_zero_handling(code)
        assert result["valid"] is True
        assert result["has_http_exception"] is True

    def test_with_zero_check(self):
        """Test code with explicit zero check."""
        code = '''
def divide(a, b):
    if b == 0:
        return {"error": "division by zero"}
    return a / b
'''
        result = validate_divide_by_zero_handling(code)
        assert result["valid"] is True

    def test_missing_handling(self):
        """Test code without divide-by-zero handling."""
        code = '''
def divide(a, b):
    return a / b
'''
        result = validate_divide_by_zero_handling(code)
        assert result["valid"] is False


class TestValidateRequirementsTxt:
    """Test cases for requirements.txt validation."""

    def test_all_deps_present(self):
        """Test when all required dependencies are present."""
        content = '''
fastapi==0.100.0
uvicorn[standard]==0.23.0
pytest>=7.0.0
httpx>=0.24.0
pydantic>=2.0.0
'''
        result = validate_requirements_txt(content)
        assert result["valid"] is True
        assert len(result["missing_deps"]) == 0

    def test_missing_deps(self):
        """Test when some dependencies are missing."""
        content = "fastapi==0.100.0"
        result = validate_requirements_txt(content)
        assert result["valid"] is False
        assert "uvicorn" in result["missing_deps"]
        assert "pytest" in result["missing_deps"]
        assert "httpx" in result["missing_deps"]


class TestValidateSyntax:
    """Test cases for Python syntax validation."""

    def test_valid_syntax(self):
        """Test valid Python code."""
        code = '''
def hello():
    return "Hello, World!"
'''
        result = validate_syntax(code, "test.py")
        assert result["valid"] is True
        assert result["error"] is None

    def test_invalid_syntax(self):
        """Test invalid Python code."""
        code = '''
def hello(:
    return "broken"
'''
        result = validate_syntax(code, "test.py")
        assert result["valid"] is False
        assert result["error"] is not None


class TestRunFailFastValidation:
    """Test cases for fail-fast validation."""

    def test_valid_files(self):
        """Test with valid generated files."""
        files = {
            "main.py": '''
from fastapi import FastAPI, HTTPException

app = FastAPI()

@app.post("/api/calculate/add")
def add(a: int, b: int):
    return {"result": a + b}

@app.post("/api/calculate/subtract")
def subtract(a: int, b: int):
    return {"result": a - b}

@app.post("/api/calculate/multiply")
def multiply(a: int, b: int):
    return {"result": a * b}

@app.post("/api/calculate/divide")
def divide(a: int, b: int):
    if b == 0:
        raise HTTPException(status_code=400, detail="Division by zero")
    return {"result": a / b}
''',
            "models.py": '''
from pydantic import BaseModel

class CalculateRequest(BaseModel):
    a: float
    b: float
''',
            "requirements.txt": '''
fastapi>=0.100.0
uvicorn[standard]>=0.23.0
pytest>=7.0.0
httpx>=0.24.0
'''
        }
        
        result = run_fail_fast_validation(files)
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_missing_main_py(self):
        """Test with missing main.py."""
        files = {
            "models.py": "class Model: pass",
            "requirements.txt": "fastapi"
        }
        
        result = run_fail_fast_validation(files)
        assert result["valid"] is False
        assert any("main.py" in e for e in result["errors"])

    def test_writes_error_txt(self):
        """Test that error.txt is written on validation failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            files = {
                "main.py": "def broken(:",  # Syntax error
            }
            
            result = run_fail_fast_validation(files, output_dir=tmpdir)
            
            assert result["valid"] is False
            error_path = Path(tmpdir) / "error.txt"
            assert error_path.exists()


class TestValidateDockerfile:
    """Test cases for Dockerfile validation."""
    
    def test_valid_dockerfile(self):
        """Test valid Dockerfile with FROM and CMD."""
        from generator.main.provenance import validate_dockerfile
        
        content = '''FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["python", "main.py"]
'''
        result = validate_dockerfile(content)
        assert result["valid"] is True
        assert result["has_from"] is True
        assert result["has_cmd_or_entrypoint"] is True
    
    def test_dockerfile_with_entrypoint(self):
        """Test Dockerfile with ENTRYPOINT instead of CMD."""
        from generator.main.provenance import validate_dockerfile
        
        content = '''FROM node:18-alpine
WORKDIR /app
COPY . .
ENTRYPOINT ["node", "index.js"]
'''
        result = validate_dockerfile(content)
        assert result["valid"] is True
        assert result["has_cmd_or_entrypoint"] is True
    
    def test_dockerfile_missing_from(self):
        """Test Dockerfile without FROM directive."""
        from generator.main.provenance import validate_dockerfile
        
        content = '''WORKDIR /app
COPY . .
CMD ["python", "main.py"]
'''
        result = validate_dockerfile(content)
        assert result["valid"] is False
        assert "FROM" in result["errors"][0]
    
    def test_empty_dockerfile(self):
        """Test empty Dockerfile."""
        from generator.main.provenance import validate_dockerfile
        
        result = validate_dockerfile("")
        assert result["valid"] is False


class TestValidateDockerCompose:
    """Test cases for docker-compose.yml validation."""
    
    def test_valid_compose(self):
        """Test valid docker-compose.yml."""
        from generator.main.provenance import validate_docker_compose
        
        content = '''version: '3.8'
services:
  app:
    build: .
    ports:
      - "8000:8000"
'''
        result = validate_docker_compose(content)
        assert result["valid"] is True
        assert result["has_services"] is True
        assert result["has_version"] is True
    
    def test_compose_without_version(self):
        """Test docker-compose without version (valid in newer specs)."""
        from generator.main.provenance import validate_docker_compose
        
        content = '''services:
  app:
    build: .
'''
        result = validate_docker_compose(content)
        assert result["valid"] is True
        assert result["has_version"] is False
    
    def test_compose_missing_services(self):
        """Test docker-compose without services."""
        from generator.main.provenance import validate_docker_compose
        
        content = '''version: '3.8'
# No services defined
'''
        result = validate_docker_compose(content)
        assert result["valid"] is False


class TestValidateDeploymentArtifacts:
    """Test cases for deployment artifact validation."""
    
    def test_valid_deployment(self):
        """Test with valid deployment files."""
        from generator.main.provenance import validate_deployment_artifacts
        
        files = {
            "Dockerfile": '''FROM python:3.11-slim
CMD ["python", "main.py"]
''',
            "docker-compose.yml": '''services:
  app:
    build: .
'''
        }
        
        result = validate_deployment_artifacts(files)
        assert result["valid"] is True
    
    def test_invalid_dockerfile(self):
        """Test with invalid Dockerfile."""
        from generator.main.provenance import validate_deployment_artifacts
        
        files = {
            "Dockerfile": "# Empty dockerfile without FROM",
        }
        
        result = validate_deployment_artifacts(files)
        assert result["valid"] is False
