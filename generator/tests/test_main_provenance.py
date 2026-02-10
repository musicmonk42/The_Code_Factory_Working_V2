# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for the provenance tracking module.
"""

import json
import tempfile
from pathlib import Path

import pytest

from generator.main.provenance import (
    PipelineStage,
    ProvenanceTracker,
    extract_endpoints_from_code,
    extract_endpoints_from_md,
    run_fail_fast_validation,
    validate_deployment_artifacts,
    validate_docker_compose,
    validate_dockerfile,
    validate_has_content,
    validate_spec_fidelity,
    validate_syntax,
)


class TestProvenanceTracker:
    """Test ProvenanceTracker class."""

    def test_init_with_job_id(self):
        tracker = ProvenanceTracker(job_id="test-123")
        assert tracker.job_id == "test-123"
        assert tracker.stages == []
        assert tracker.artifacts == {}

    def test_init_generates_job_id(self):
        tracker = ProvenanceTracker()
        assert tracker.job_id.startswith("job-")

    def test_compute_sha256(self):
        content = "Hello, World!"
        sha256 = ProvenanceTracker.compute_sha256(content)
        # Verify it's a valid SHA256 hex string (64 chars)
        assert len(sha256) == 64
        assert all(c in '0123456789abcdef' for c in sha256)
        # Verify consistency
        assert sha256 == ProvenanceTracker.compute_sha256(content)

    def test_record_stage(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_stage(
            PipelineStage.READ_MD,
            artifacts={"input.md": "# Test"},
            metadata={"source": "test"}
        )
        
        assert len(tracker.stages) == 1
        assert tracker.stages[0]["stage"] == "READ_MD"
        assert "input.md" in tracker.stages[0]["artifacts"]

    def test_record_error(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_error("STAGE1", "TestError", "Test message")
        
        assert len(tracker.errors) == 1
        assert tracker.errors[0]["error_type"] == "TestError"

    def test_artifact_change_detection(self):
        tracker = ProvenanceTracker(job_id="test")
        
        tracker.record_stage("S1", artifacts={"file.py": "v1"})
        tracker.record_stage("S2", artifacts={"file.py": "v1"})
        assert not tracker.check_artifact_changed("file.py")
        
        tracker.record_stage("S3", artifacts={"file.py": "v2"})
        assert tracker.check_artifact_changed("file.py")

    def test_get_overwrites(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_stage("S1", artifacts={"a.py": "v1", "b.py": "stable"})
        tracker.record_stage("S2", artifacts={"a.py": "v2", "b.py": "stable"})
        
        overwrites = tracker.get_artifact_overwrites()
        assert "a.py" in overwrites
        assert "b.py" not in overwrites

    def test_to_dict(self):
        tracker = ProvenanceTracker(job_id="test")
        tracker.record_stage("S1", artifacts={"test.py": "content"})
        
        data = tracker.to_dict()
        assert data["job_id"] == "test"
        assert "stages" in data
        assert "summary" in data

    def test_save_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = ProvenanceTracker(job_id="test")
            tracker.record_stage("S1", artifacts={"test.py": "content"})
            
            path = tracker.save_to_file(tmpdir)
            assert Path(path).exists()
            
            with open(path) as f:
                data = json.load(f)
            assert data["job_id"] == "test"


class TestValidateSyntax:
    """Test syntax validation."""

    def test_valid_syntax(self):
        code = "def hello(): return 'world'"
        result = validate_syntax(code, "test.py")
        assert result["valid"] is True

    def test_invalid_syntax(self):
        code = "def hello(:"
        result = validate_syntax(code, "test.py")
        assert result["valid"] is False
        assert result["error"] is not None


class TestValidateHasContent:
    """Test content validation."""

    def test_has_content(self):
        result = validate_has_content("def main(): pass", "main.py")
        assert result["valid"] is True

    def test_empty_content(self):
        result = validate_has_content("", "main.py")
        assert result["valid"] is False


class TestExtractEndpoints:
    """Test endpoint extraction."""

    def test_extract_fastapi_endpoints(self):
        code = '''
@app.get("/users")
def get_users(): pass

@app.post("/users")
def create_user(): pass
'''
        endpoints = extract_endpoints_from_code(code)
        assert len(endpoints) == 2
        assert any(e["path"] == "/users" and e["method"] == "GET" for e in endpoints)
        assert any(e["path"] == "/users" and e["method"] == "POST" for e in endpoints)


class TestRunFailFastValidation:
    """Test fail-fast validation."""

    def test_valid_files(self):
        files = {
            "main.py": "def main(): pass",
            "requirements.txt": "fastapi\nuvicorn"
        }
        result = run_fail_fast_validation(files)
        assert result["valid"] is True

    def test_missing_main(self):
        files = {"requirements.txt": "fastapi"}
        result = run_fail_fast_validation(files)
        assert result["valid"] is False
        assert any("main.py" in e for e in result["errors"])

    def test_syntax_error(self):
        files = {
            "main.py": "def broken(:",
            "requirements.txt": "fastapi"
        }
        result = run_fail_fast_validation(files)
        assert result["valid"] is False


class TestValidateDockerfile:
    """Test Dockerfile validation."""

    def test_valid_dockerfile(self):
        content = "FROM python:3.11\nCMD python main.py"
        result = validate_dockerfile(content)
        assert result["valid"] is True

    def test_missing_from(self):
        content = "CMD python main.py"
        result = validate_dockerfile(content)
        assert result["valid"] is False

    def test_empty(self):
        result = validate_dockerfile("")
        assert result["valid"] is False


class TestValidateDockerCompose:
    """Test docker-compose validation."""

    def test_valid_compose(self):
        content = "services:\n  app:\n    build: ."
        result = validate_docker_compose(content)
        assert result["valid"] is True

    def test_missing_services(self):
        content = "version: '3'"
        result = validate_docker_compose(content)
        assert result["valid"] is False


class TestExtractEndpointsFromMd:
    """Test MD spec endpoint extraction."""

    def test_extract_basic_endpoints(self):
        md = """
# API Spec
- GET /api/users
- POST /api/users
- DELETE /api/users/{id}
"""
        endpoints = extract_endpoints_from_md(md)
        assert len(endpoints) == 3
        assert any(e["method"] == "GET" and e["path"] == "/api/users" for e in endpoints)
        assert any(e["method"] == "POST" and e["path"] == "/api/users" for e in endpoints)
        assert any(e["method"] == "DELETE" for e in endpoints)

    def test_extract_table_format(self):
        md = """
| Method | Path | Description |
|--------|------|-------------|
| GET | /api/items | Get all items |
| POST | /api/items | Create item |
"""
        endpoints = extract_endpoints_from_md(md)
        assert len(endpoints) == 2
        assert any(e["method"] == "GET" and e["path"] == "/api/items" for e in endpoints)

    def test_extract_backtick_format(self):
        md = """
The API has the following endpoints:
`GET /api/products`
`POST /api/products`
"""
        endpoints = extract_endpoints_from_md(md)
        assert len(endpoints) == 2

    def test_empty_md(self):
        endpoints = extract_endpoints_from_md("")
        assert endpoints == []

    def test_no_duplicates(self):
        md = """
- GET /api/users
- GET /api/users
- **GET** /api/users
"""
        endpoints = extract_endpoints_from_md(md)
        assert len(endpoints) == 1


class TestValidateSpecFidelity:
    """Test spec fidelity validation."""

    def test_all_endpoints_present(self):
        md = """
- GET /api/users
- POST /api/users
"""
        files = {
            "main.py": '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/api/users")
def get_users(): pass

@app.post("/api/users")
def create_user(): pass
''',
            "requirements.txt": "fastapi"
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True
        assert len(result["missing_endpoints"]) == 0

    def test_missing_endpoints(self):
        md = """
- GET /api/users
- POST /api/users
- DELETE /api/users/{id}
"""
        files = {
            "main.py": '''
@app.get("/api/users")
def get_users(): pass
''',
            "requirements.txt": "fastapi"
        }
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is False
        assert len(result["missing_endpoints"]) == 2

    def test_no_endpoints_in_spec(self):
        md = "# Simple README\nNo API endpoints here."
        files = {"main.py": "print('hello')", "requirements.txt": ""}
        result = validate_spec_fidelity(md, files)
        assert result["valid"] is True  # No endpoints required = pass

    def test_writes_error_file(self):
        md = "- GET /api/missing"
        files = {"main.py": "pass", "requirements.txt": ""}
        with tempfile.TemporaryDirectory() as tmpdir:
            result = validate_spec_fidelity(md, files, output_dir=tmpdir)
            assert result["valid"] is False
            error_path = Path(tmpdir) / "error.txt"
            assert error_path.exists()


class TestValidateDeploymentArtifacts:
    """Test deployment validation."""

    def test_valid_deployment(self):
        files = {
            "Dockerfile": "FROM python:3.11\nCMD python main.py",
            "docker-compose.yml": "services:\n  app:\n    build: ."
        }
        result = validate_deployment_artifacts(files)
        assert result["valid"] is True

    def test_invalid_dockerfile(self):
        files = {"Dockerfile": "# no FROM"}
        result = validate_deployment_artifacts(files)
        assert result["valid"] is False


class TestExtractOutputDir:
    """Test extract_output_dir_from_md function."""

    def test_extracts_simple_output_dir(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
# Project Spec
output_dir: generated/hello_generator

## API Endpoints
"""
        result = extract_output_dir_from_md(md_content)
        assert result == "generated/hello_generator"

    def test_extracts_output_dir_with_quotes(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
output_dir: "my_project"
"""
        result = extract_output_dir_from_md(md_content)
        assert result == "my_project"

    def test_rejects_path_traversal(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
output_dir: ../../../etc/passwd
"""
        result = extract_output_dir_from_md(md_content)
        assert result == ""

    def test_rejects_absolute_paths(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
output_dir: /absolute/path
"""
        result = extract_output_dir_from_md(md_content)
        assert result == ""

    def test_rejects_windows_absolute_paths(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
output_dir: C:/windows/path
"""
        result = extract_output_dir_from_md(md_content)
        assert result == ""

    def test_returns_empty_when_not_found(self):
        from generator.main.provenance import extract_output_dir_from_md
        
        md_content = """
# Project without output_dir
"""
        result = extract_output_dir_from_md(md_content)
        assert result == ""


class TestValidateReadmeCompleteness:
    """Test validate_readme_completeness function."""

    def test_valid_complete_readme(self):
        from generator.main.provenance import validate_readme_completeness
        
        readme = """
# My Project

This is a comprehensive README for my project with detailed instructions.

## Setup

1. Create virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\\Scripts\\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Run the Server

Start the development server:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at http://localhost:8000

## Testing

Run the test suite:
```bash
pytest tests/ -v --cov=app
```

## API Examples

### Health Check
```bash
curl http://localhost:8000/health
```

### Get Users
```bash
curl http://localhost:8000/api/users
```
"""
        result = validate_readme_completeness(readme)
        assert result["valid"] is True
        assert result["length"] > 500
        assert "setup" in result["sections_found"]
        assert "run" in result["sections_found"]
        assert "test" in result["sections_found"]
        assert "examples" in result["sections_found"]
        assert "venv" in result["commands_found"]
        assert "pip" in result["commands_found"]
        assert "uvicorn" in result["commands_found"]
        assert "pytest" in result["commands_found"]

    def test_incomplete_readme_too_short(self):
        from generator.main.provenance import validate_readme_completeness
        
        readme = "# Short README"
        result = validate_readme_completeness(readme)
        assert result["valid"] is False
        assert "too short" in str(result["errors"])

    def test_incomplete_readme_missing_sections(self):
        from generator.main.provenance import validate_readme_completeness
        
        # Create a README that's long enough but missing required sections
        readme = "# Project\n\n" + ("This is filler content to meet the minimum length requirement. " * 20)
        result = validate_readme_completeness(readme)
        assert result["valid"] is False
        assert any("setup" in err.lower() for err in result["errors"])

    def test_incomplete_readme_missing_commands(self):
        from generator.main.provenance import validate_readme_completeness
        
        # Create README with sections but no commands
        readme_parts = [
            "# Project\n\n",
            "## Setup\n",
            "Some setup instructions. " * 10,
            "\n\n## Run\n",
            "Run the app. " * 10,
            "\n\n## Testing\n",
            "Test the app. " * 10,
            "\n\n## Examples\n",
            "Some examples. " * 10,
        ]
        readme = "".join(readme_parts)
        result = validate_readme_completeness(readme)
        assert result["valid"] is False
        # Should be missing venv, pip, uvicorn, pytest commands
        assert any("venv" in err.lower() for err in result["errors"])
